"""
Status monitoring cog - polls Chub API and maintains status embed.
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List, Deque
from collections import deque

from utils import ChubAPIClient, ChubStatus, Database

logger = logging.getLogger(__name__)


class StatusHistory:
    """Maintains rolling history of status snapshots for visual display."""
    
    def __init__(self, max_depth: int = 10):
        self.max_depth = max_depth
        # model_name -> deque of health values ('green', 'orange', 'red')
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
    
    def get_visual(self, model_name: str) -> str:
        """Get emoji string showing recent status history."""
        if model_name not in self.history:
            return ""
        
        emoji_map = {'green': '🟢', 'orange': '🟠', 'red': '🔴', 'unknown': '⚪'}
        return ''.join(emoji_map.get(h, '⚪') for h in self.history[model_name])


class StatusCog(commands.Cog):
    """Cog for monitoring and displaying Chub.ai status."""
    
    def __init__(
        self, 
        bot: commands.Bot,
        chub_client: ChubAPIClient,
        database: Database,
        poll_interval: int = 10,
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
    
    @tasks.loop(seconds=10)  # Interval set in cog_load
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
            
            # Update all status embeds
            await self._update_all_embeds(status)
            
            logger.info(f"Status updated: API={status.api_health}, models={len(status.models)}")
            
        except Exception as e:
            logger.error(f"Error in status loop: {e}", exc_info=True)
    
    @status_loop.before_loop
    async def before_status_loop(self) -> None:
        """Wait for bot to be ready before starting loop."""
        await self.bot.wait_until_ready()
        
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
        
        embed = self._build_status_embed(status)
        
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
            logger.info(f"Created new status message in guild {guild_id}")
        except discord.HTTPException as e:
            logger.error(f"Failed to send status message: {e}")
    
    def _build_status_embed(self, status: ChubStatus) -> discord.Embed:
        """Build the status embed with visual history."""
        # Determine overall color
        if status.api_health == 'red' or any(m.health == 'red' for m in status.models):
            color = discord.Color.red()
        elif status.api_health == 'orange' or any(m.health == 'orange' for m in status.models):
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
        
        # Footer - timestamp will appear after this automatically
        embed.set_footer(text="Last update from Chub (5 min interval)")
        
        return embed
    
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
        
        if status:
            self.status_history.add_snapshot(status)
            embed = self._build_status_embed(status)
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
        
        return message


async def setup(bot: commands.Bot) -> None:
    """Setup function for loading cog (called by bot.load_extension)."""
    pass
