"""
Status monitoring cog - polls Chub API, maintains status embed, handles notifications.
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Deque
from collections import deque
from dataclasses import dataclass, field

from utils import ChubAPIClient, ChubStatus, Database
from utils.chub_api import MODEL_ORDER

logger = logging.getLogger(__name__)

# Notification thresholds (in Chub API updates, NOT our polls)
# Chub updates every ~5 minutes
DOWN_THRESHOLD = 2       # 2 red ticks ≈ 10 minutes before alerting
RECOVERY_THRESHOLD = 3   # 3 green ticks ≈ 15 minutes before recovery alert


@dataclass
class ModelAlertState:
    """Tracks alert state for a single model."""
    consecutive_red: int = 0
    consecutive_green: int = 0
    is_notified: bool = False


class StatusHistory:
    """Maintains rolling history of status snapshots for visual display."""
    
    def __init__(self, max_depth: int = 10):
        self.max_depth = max_depth
        # model_name -> deque of health values ('green', 'yellow', 'red')
        self.history: Dict[str, Deque[str]] = {}
        self.last_timestamp: Optional[datetime] = None
    
    def add_snapshot(self, status: ChubStatus) -> bool:
        """
        Add a status snapshot to history.
        
        Returns True if this is new data (timestamp changed).
        """
        # Check if timestamp changed
        if self.last_timestamp and status.timestamp <= self.last_timestamp:
            return False
        
        self.last_timestamp = status.timestamp
        
        # Add model health
        for model in status.models:
            if model.name not in self.history:
                self.history[model.name] = deque(maxlen=self.max_depth)
            self.history[model.name].append(model.health)
        
        return True
    
    def load_from_list(self, model_name: str, health_list: List[str]) -> None:
        """Load history from a list (used for DB restore on startup)."""
        if model_name not in self.history:
            self.history[model_name] = deque(maxlen=self.max_depth)
        
        for health in health_list:
            self.history[model_name].append(health)
    
    def get_visual(self, model_name: str) -> str:
        """Get emoji string showing recent status history."""
        if model_name not in self.history:
            return ""
        
        emoji_map = {'green': '🟢', 'yellow': '🟡', 'orange': '🟠', 'red': '🔴', 'unknown': '⚪'}
        return ''.join(emoji_map.get(h, '⚪') for h in self.history[model_name])


class StatusCog(commands.Cog):
    """Cog for monitoring and displaying Chub.ai status."""
    
    def __init__(
        self, 
        bot: commands.Bot,
        chub_client: ChubAPIClient,
        database: Database,
        poll_interval: int = 60,
        history_depth: int = 10
    ):
        self.bot = bot
        self.chub = chub_client
        self.db = database
        self.poll_interval = poll_interval
        
        self.status_history = StatusHistory(max_depth=history_depth)
        self.history_depth = history_depth
        
        # Track status messages per guild
        self.status_messages: Dict[int, discord.Message] = {}
        
        # Alert states per guild per model
        # guild_id -> model_name -> ModelAlertState
        self.alert_states: Dict[int, Dict[str, ModelAlertState]] = {}
        
        # Reaction debounce: user_id -> last_change_time
        self.reaction_cooldowns: Dict[int, datetime] = {}
        self.REACTION_COOLDOWN_SECONDS = 5
    
    async def cog_load(self) -> None:
        """Called when cog is loaded."""
        await self.chub.start()
        self.status_loop.change_interval(seconds=self.poll_interval)
        self.status_loop.start()
        logger.info(f"Status cog loaded, polling every {self.poll_interval}s")
    
    async def cog_unload(self) -> None:
        """Called when cog is unloaded."""
        self.status_loop.cancel()
        await self.chub.close()
        logger.info("Status cog unloaded")
    
    @tasks.loop(seconds=60)  # Interval set in cog_load
    async def status_loop(self) -> None:
        """Main polling loop for status updates."""
        try:
            status, changed = await self.chub.fetch_if_changed()
            
            if not status:
                logger.debug("No status received or fetch failed")
                return
            
            if not changed:
                logger.debug("Status unchanged, skipping update")
                return
            
            # Add to history
            is_new = self.status_history.add_snapshot(status)
            
            if not is_new:
                logger.debug("Duplicate timestamp, skipping")
                return
            
            # Log to database
            for model in status.models:
                await self.db.log_status(
                    api_health=status.api_health,
                    model_name=model.name,
                    model_health=model.health,
                    avg_latency=model.avg_latency,
                    timeout_pct=model.timeout_pct,
                    fail_pct=model.fail_pct
                )
                
                # Log yellow as info
                if model.health == 'yellow':
                    logger.info(f"Model {model.name} is degraded (yellow)")
            
            # Update all status embeds
            await self._update_all_embeds(status)
            
            # Process notifications
            await self._process_notifications(status)
            
            logger.info(f"Status updated: API={status.api_health}, models={len(status.models)}")
            
        except Exception as e:
            logger.error(f"Error in status loop: {e}", exc_info=True)
    
    @status_loop.before_loop
    async def before_status_loop(self) -> None:
        """Wait for bot to be ready before starting loop."""
        await self.bot.wait_until_ready()
        
        # Load history from database for each model
        await self._load_history_from_db()
        
        # Restore status message references from database
        configs = await self.db.get_all_status_channels()
        for config in configs:
            guild_id = config['guild_id']
            channel_id = config['status_channel_id']
            message_id = config.get('status_message_id')
            
            if channel_id and message_id:
                try:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        message = await channel.fetch_message(message_id)
                        self.status_messages[guild_id] = message
                        logger.info(f"Restored status message for guild {guild_id}")
                except discord.NotFound:
                    logger.warning(f"Status message {message_id} not found for guild {guild_id}")
                except Exception as e:
                    logger.error(f"Error restoring message for guild {guild_id}: {e}")
        
        # Load alert states from database
        await self._load_alert_states_from_db()
    
    async def _load_history_from_db(self) -> None:
        """Load status history from database on startup."""
        for model_name in MODEL_ORDER:
            health_list = await self.db.get_recent_model_health(model_name, self.history_depth)
            if health_list:
                self.status_history.load_from_list(model_name, health_list)
                logger.debug(f"Loaded {len(health_list)} history entries for {model_name}")
        
        logger.info("Status history loaded from database")
    
    async def _load_alert_states_from_db(self) -> None:
        """Load alert states from database on startup."""
        configs = await self.db.get_all_notification_configs()
        
        for config in configs:
            guild_id = config['guild_id']
            states = await self.db.get_all_alert_states(guild_id)
            
            if states:
                self.alert_states[guild_id] = {}
                for model_name, state_data in states.items():
                    self.alert_states[guild_id][model_name] = ModelAlertState(
                        consecutive_red=state_data['consecutive_red'],
                        consecutive_green=state_data['consecutive_green'],
                        is_notified=state_data['is_notified']
                    )
                logger.debug(f"Loaded alert states for guild {guild_id}")
        
        logger.info("Alert states loaded from database")
    
    async def _update_all_embeds(self, status: ChubStatus) -> None:
        """Update status embeds in all configured channels."""
        configs = await self.db.get_all_status_channels()
        
        for config in configs:
            guild_id = config['guild_id']
            channel_id = config['status_channel_id']
            
            try:
                await self._update_guild_embed(guild_id, channel_id, status)
            except Exception as e:
                logger.error(f"Failed to update embed for guild {guild_id}: {e}")
    
    async def _update_guild_embed(
        self, 
        guild_id: int, 
        channel_id: int, 
        status: ChubStatus
    ) -> None:
        """Update or create status embed for a specific guild."""
        channel = self.bot.get_channel(channel_id)
        if not channel:
            logger.warning(f"Channel {channel_id} not found for guild {guild_id}")
            return
        
        # Get notification config for subscription section
        notif_config = await self.db.get_notification_config(guild_id)
        embed = self._build_status_embed(status, notif_config)
        
        # Try to edit existing message
        if guild_id in self.status_messages:
            try:
                await self.status_messages[guild_id].edit(embed=embed)
                return
            except discord.NotFound:
                # Message was deleted, create new one
                del self.status_messages[guild_id]
            except discord.HTTPException as e:
                logger.error(f"Failed to edit message: {e}")
        
        # Create new message
        try:
            message = await channel.send(embed=embed)
            self.status_messages[guild_id] = message
            await self.db.set_status_message(guild_id, message.id)
            
            # Add reaction emoji if notifications configured
            if notif_config and notif_config.get('notification_channel_id'):
                emoji = notif_config.get('notification_emoji', '🔔')
                try:
                    await message.add_reaction(emoji)
                except discord.HTTPException:
                    logger.warning(f"Could not add reaction {emoji} to status message")
            
            logger.info(f"Created new status message in guild {guild_id}")
        except discord.HTTPException as e:
            logger.error(f"Failed to send status message: {e}")
    
    def _build_status_embed(
        self, 
        status: ChubStatus, 
        notif_config: Optional[Dict] = None
    ) -> discord.Embed:
        """Build the status embed with visual history."""
        # Determine overall color
        if status.api_health == 'red' or any(m.health == 'red' for m in status.models):
            color = discord.Color.red()
        elif status.api_health in ('orange', 'yellow') or any(m.health in ('orange', 'yellow') for m in status.models):
            color = discord.Color.orange()
        else:
            color = discord.Color.green()
        
        embed = discord.Embed(
            title="Chub.ai Status",
            color=color,
            timestamp=discord.utils.utcnow()  # Shows user's local time after footer
        )
        
        # API status - single emoji only
        api_emoji = status.api_emoji
        api_text = "Healthy" if status.api_health == "green" else status.api_health.capitalize()
        embed.add_field(
            name="API",
            value=f"{api_emoji} {api_text}",
            inline=False
        )
        
        # Model statuses with history - health dots only
        model_lines = []
        for model in status.models:
            history_visual = self.status_history.get_visual(model.name)
            name_display = model.name.capitalize()
            line = f"`{name_display.ljust(8)}` {history_visual}"
            model_lines.append(line)
        
        embed.add_field(
            name="Models (← older │ newer →)",
            value="\n".join(model_lines) if model_lines else "No model data",
            inline=False
        )
        
        # Link to full status page
        embed.add_field(
            name="\u200b",  # Zero-width space for spacing
            value="For more information: https://chub.ai/status",
            inline=False
        )
        
        # Subscription section if notifications configured
        if notif_config and notif_config.get('notification_channel_id'):
            emoji = notif_config.get('notification_emoji', '🔔')
            embed.add_field(
                name="Outage Alerts",
                value=f"React with {emoji} to subscribe to outage notifications.\nRemove your reaction to unsubscribe.",
                inline=False
            )
        
        # Footer - timestamp will appear after this automatically
        embed.set_footer(text="Last update from Chub (5 min interval)")
        
        return embed
    
    async def _process_notifications(self, status: ChubStatus) -> None:
        """Process notification logic for all configured guilds."""
        configs = await self.db.get_all_notification_configs()
        
        for config in configs:
            guild_id = config['guild_id']
            channel_id = config['notification_channel_id']
            role_id = config['notification_role_id']
            
            if not channel_id or not role_id:
                continue
            
            try:
                await self._process_guild_notifications(guild_id, channel_id, role_id, status)
            except Exception as e:
                logger.error(f"Error processing notifications for guild {guild_id}: {e}")
    
    async def _process_guild_notifications(
        self,
        guild_id: int,
        channel_id: int,
        role_id: int,
        status: ChubStatus
    ) -> None:
        """Process notifications for a single guild."""
        # Initialize guild alert states if not exists
        if guild_id not in self.alert_states:
            self.alert_states[guild_id] = {}
        
        models_now_down = []
        models_now_recovered = []
        
        for model in status.models:
            model_name = model.name
            
            # Initialize model state if not exists
            if model_name not in self.alert_states[guild_id]:
                self.alert_states[guild_id][model_name] = ModelAlertState()
            
            state = self.alert_states[guild_id][model_name]
            
            if model.health == 'red':
                state.consecutive_red += 1
                state.consecutive_green = 0
                
                logger.debug(f"Model {model_name} red tick #{state.consecutive_red} (threshold: {DOWN_THRESHOLD})")
                
                # Check if we should send down notification
                if state.consecutive_red >= DOWN_THRESHOLD and not state.is_notified:
                    models_now_down.append(model_name)
                    state.is_notified = True
                    logger.info(f"Model {model_name} marked as down for guild {guild_id}")
            
            elif model.health == 'green':
                state.consecutive_red = 0
                
                # Only count green toward recovery if we've already notified
                if state.is_notified:
                    state.consecutive_green += 1
                    
                    logger.debug(f"Model {model_name} green tick #{state.consecutive_green} (threshold: {RECOVERY_THRESHOLD})")
                    
                    # Check if we should send recovery notification
                    if state.consecutive_green >= RECOVERY_THRESHOLD:
                        models_now_recovered.append(model_name)
                        state.is_notified = False
                        state.consecutive_green = 0
                        logger.info(f"Model {model_name} marked as recovered for guild {guild_id}")
            
            else:
                # Yellow/orange: neutral - don't reset counters, don't count toward anything
                logger.debug(f"Model {model_name} is {model.health} (neutral - no counter changes)")
            
            # Persist state to database
            await self.db.update_alert_state(
                guild_id=guild_id,
                model_name=model_name,
                consecutive_red=state.consecutive_red,
                consecutive_green=state.consecutive_green,
                is_notified=state.is_notified
            )
        
        # Send batched notifications
        channel = self.bot.get_channel(channel_id)
        if not channel:
            logger.warning(f"Notification channel {channel_id} not found for guild {guild_id}")
            return
        
        guild = self.bot.get_guild(guild_id)
        role = guild.get_role(role_id) if guild else None
        role_mention = role.mention if role else ""
        
        if models_now_down:
            model_list = ", ".join(m.capitalize() for m in models_now_down)
            embed = discord.Embed(
                title="🔴 Model Outage Detected",
                description=f"The following models have been degraded/down for 10+ minutes:\n**{model_list}**",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            embed.set_footer(text="Chub.ai Status Bot")
            
            try:
                await channel.send(content=role_mention, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))
                logger.info(f"Sent down notification for {models_now_down} in guild {guild_id}")
            except discord.HTTPException as e:
                logger.error(f"Failed to send down notification: {e}")
        
        if models_now_recovered:
            model_list = ", ".join(m.capitalize() for m in models_now_recovered)
            embed = discord.Embed(
                title="🟢 Models Recovered",
                description=f"The following models are back online:\n**{model_list}**",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            embed.set_footer(text="Chub.ai Status Bot")
            
            try:
                await channel.send(content=role_mention, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))
                logger.info(f"Sent recovery notification for {models_now_recovered} in guild {guild_id}")
            except discord.HTTPException as e:
                logger.error(f"Failed to send recovery notification: {e}")
    
    # --- Reaction Handlers for Subscription ---
    
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Handle reaction add for subscription."""
        await self._handle_reaction(payload, add=True)
    
    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        """Handle reaction remove for unsubscription."""
        await self._handle_reaction(payload, add=False)
    
    async def _handle_reaction(self, payload: discord.RawReactionActionEvent, add: bool) -> None:
        """Handle reaction add/remove for subscription management."""
        # Ignore bot reactions
        if payload.user_id == self.bot.user.id:
            return
        
        guild_id = payload.guild_id
        if not guild_id:
            return
        
        # Check if this is our status message
        if guild_id not in self.status_messages:
            return
        
        if payload.message_id != self.status_messages[guild_id].id:
            return
        
        # Get notification config
        config = await self.db.get_notification_config(guild_id)
        if not config or not config.get('notification_role_id'):
            return
        
        # Check if it's the right emoji
        expected_emoji = config.get('notification_emoji', '🔔')
        reaction_emoji = str(payload.emoji)
        
        if reaction_emoji != expected_emoji:
            return
        
        # Debounce check
        now = datetime.now()
        last_change = self.reaction_cooldowns.get(payload.user_id)
        if last_change and (now - last_change).total_seconds() < self.REACTION_COOLDOWN_SECONDS:
            logger.debug(f"Debounce: ignoring reaction from user {payload.user_id}")
            return
        
        self.reaction_cooldowns[payload.user_id] = now
        
        # Get guild and member
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        
        try:
            member = await guild.fetch_member(payload.user_id)
        except discord.NotFound:
            return
        except discord.HTTPException as e:
            logger.error(f"Failed to fetch member {payload.user_id}: {e}")
            return
        
        role = guild.get_role(config['notification_role_id'])
        if not role:
            logger.warning(f"Notification role {config['notification_role_id']} not found in guild {guild_id}")
            return
        
        try:
            if add:
                await member.add_roles(role, reason="Subscribed to status notifications")
                logger.info(f"Added notification role to user {payload.user_id} in guild {guild_id}")
            else:
                await member.remove_roles(role, reason="Unsubscribed from status notifications")
                logger.info(f"Removed notification role from user {payload.user_id} in guild {guild_id}")
        except discord.Forbidden:
            logger.error(f"Missing permissions to manage roles in guild {guild_id}")
        except discord.HTTPException as e:
            logger.error(f"Failed to modify roles for user {payload.user_id}: {e}")
    
    # --- Public Methods ---
    
    async def initialize_status_channel(
        self, 
        guild_id: int, 
        channel: discord.TextChannel
    ) -> discord.Message:
        """
        Initialize status monitoring in a channel.
        
        Creates the initial embed and saves configuration.
        """
        # Fetch initial status
        status = await self.chub.fetch_status()
        
        # Get notification config for embed
        notif_config = await self.db.get_notification_config(guild_id)
        
        if status:
            self.status_history.add_snapshot(status)
            embed = self._build_status_embed(status, notif_config)
        else:
            # Create placeholder embed
            embed = discord.Embed(
                title="Chub.ai Status",
                description="Waiting for first status update...",
                color=discord.Color.greyple()
            )
        
        message = await channel.send(embed=embed)
        self.status_messages[guild_id] = message
        
        # Save to database
        await self.db.set_status_channel(guild_id, channel.id, message.id)
        
        # Add subscription emoji if notifications configured
        if notif_config and notif_config.get('notification_channel_id'):
            emoji = notif_config.get('notification_emoji', '🔔')
            try:
                await message.add_reaction(emoji)
            except discord.HTTPException:
                pass
        
        return message
    
    async def refresh_status_embed(self, guild_id: int) -> bool:
        """
        Refresh the status embed for a guild (e.g., after config change).
        
        Returns True if successful.
        """
        config = await self.db.get_guild_config(guild_id)
        if not config or not config.get('status_channel_id'):
            return False
        
        # Fetch current status
        status = await self.chub.fetch_status()
        if not status:
            return False
        
        # Get notification config
        notif_config = await self.db.get_notification_config(guild_id)
        
        embed = self._build_status_embed(status, notif_config)
        
        # Update the message
        if guild_id in self.status_messages:
            try:
                message = self.status_messages[guild_id]
                await message.edit(embed=embed)
                
                # Ensure reaction is present if notifications configured
                if notif_config and notif_config.get('notification_channel_id'):
                    emoji = notif_config.get('notification_emoji', '🔔')
                    # Check if bot already reacted
                    try:
                        await message.add_reaction(emoji)
                    except discord.HTTPException:
                        pass
                
                return True
            except discord.HTTPException as e:
                logger.error(f"Failed to refresh embed for guild {guild_id}: {e}")
                return False
        
        return False


async def setup(bot: commands.Bot) -> None:
    """Setup function for loading cog (called by bot.load_extension)."""
    pass