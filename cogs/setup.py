"""
Setup commands cog - admin commands for configuring the bot.
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging
from typing import Optional

from utils import Database
from cogs.status import StatusCog

logger = logging.getLogger(__name__)


class SetupCog(commands.Cog):
    """Cog for bot configuration commands."""
    
    def __init__(self, bot: commands.Bot, database: Database):
        self.bot = bot
        self.db = database
    
    setup_group = app_commands.Group(
        name="setup",
        description="Configure the bot",
        default_permissions=discord.Permissions(administrator=True)
    )
    
    @setup_group.command(name="status", description="Set the channel for the status embed")
    @app_commands.describe(channel="The channel to display status updates")
    async def setup_status(
        self, 
        interaction: discord.Interaction, 
        channel: discord.TextChannel
    ) -> None:
        """Configure status monitoring channel."""
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Check bot permissions in target channel
            permissions = channel.permissions_for(interaction.guild.me)
            if not permissions.send_messages or not permissions.embed_links:
                await interaction.followup.send(
                    f"❌ I don't have permission to send embeds in {channel.mention}",
                    ephemeral=True
                )
                return
            
            # Get the status cog and initialize
            status_cog: StatusCog = self.bot.get_cog('StatusCog')
            if not status_cog:
                await interaction.followup.send(
                    "❌ Status monitoring is not available",
                    ephemeral=True
                )
                return
            
            # Initialize the status channel
            message = await status_cog.initialize_status_channel(
                guild_id=interaction.guild_id,
                channel=channel
            )
            
            await interaction.followup.send(
                f"✅ Status monitoring configured in {channel.mention}\n"
                f"The embed will update automatically.",
                ephemeral=True
            )
            
            logger.info(f"Status channel set for guild {interaction.guild_id}: {channel.id}")
            
        except Exception as e:
            logger.error(f"Error in setup status: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Failed to configure status channel: {str(e)}",
                ephemeral=True
            )
    
    @setup_group.command(name="notifications", description="Set or clear the notification channel and role for outage alerts")
    @app_commands.describe(
        channel="The channel for outage notifications (leave empty to clear)",
        role="The role to ping and assign via reactions",
        emoji="The emoji for subscription reactions (default: 🔔)"
    )
    async def setup_notifications(
        self, 
        interaction: discord.Interaction, 
        channel: Optional[discord.TextChannel] = None,
        role: Optional[discord.Role] = None,
        emoji: Optional[str] = "🔔"
    ) -> None:
        """
        Configure outage notifications.
        
        If channel and role are provided, enables notifications.
        If channel is omitted, clears notification config.
        """
        await interaction.response.defer(ephemeral=True)
        
        try:
            if channel:
                # Validate role is also provided
                if not role:
                    await interaction.followup.send(
                        "❌ You must specify a role to ping when setting up notifications.\n"
                        "Example: `/setup notifications #alerts @Outage-Alerts`",
                        ephemeral=True
                    )
                    return
                
                # Check bot permissions in channel
                permissions = channel.permissions_for(interaction.guild.me)
                if not permissions.send_messages or not permissions.embed_links:
                    await interaction.followup.send(
                        f"❌ I don't have permission to send messages/embeds in {channel.mention}",
                        ephemeral=True
                    )
                    return
                
                # Check bot can manage the role
                if role >= interaction.guild.me.top_role:
                    await interaction.followup.send(
                        f"❌ I cannot manage {role.mention} - it's higher than or equal to my highest role.\n"
                        "Please move my role above the notification role in Server Settings → Roles.",
                        ephemeral=True
                    )
                    return
                
                # Validate emoji (basic check)
                emoji = emoji.strip() if emoji else "🔔"
                if len(emoji) > 32:
                    emoji = "🔔"
                
                # Save notification config
                await self.db.set_notification_config(
                    guild_id=interaction.guild_id,
                    channel_id=channel.id,
                    role_id=role.id,
                    emoji=emoji
                )
                
                # Refresh the status embed to show subscription section
                status_cog: StatusCog = self.bot.get_cog('StatusCog')
                if status_cog:
                    await status_cog.refresh_status_embed(interaction.guild_id)
                
                await interaction.followup.send(
                    f"✅ Notifications configured!\n"
                    f"**Channel:** {channel.mention}\n"
                    f"**Role:** {role.mention}\n"
                    f"**Emoji:** {emoji}\n\n"
                    f"Users can react with {emoji} on the status embed to subscribe.\n"
                    f"Alerts will be sent to {channel.mention} after ~10 minutes of confirmed downtime.",
                    ephemeral=True
                )
                
                logger.info(f"Notifications configured for guild {interaction.guild_id}: channel={channel.id}, role={role.id}")
            else:
                # Clear notification config
                await self.db.clear_notification_config(interaction.guild_id)
                await self.db.clear_alert_states(interaction.guild_id)
                
                # Refresh the status embed to remove subscription section
                status_cog: StatusCog = self.bot.get_cog('StatusCog')
                if status_cog:
                    await status_cog.refresh_status_embed(interaction.guild_id)
                
                await interaction.followup.send(
                    "✅ Notifications cleared for this server.\n"
                    "Users will no longer receive outage alerts.",
                    ephemeral=True
                )
                
                logger.info(f"Notifications cleared for guild {interaction.guild_id}")
            
        except Exception as e:
            logger.error(f"Error in setup notifications: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Failed to configure notifications: {str(e)}",
                ephemeral=True
            )
    
    @setup_group.command(name="view", description="View current bot configuration")
    async def setup_view(self, interaction: discord.Interaction) -> None:
        """Display current bot configuration for this guild."""
        await interaction.response.defer(ephemeral=True)
        
        try:
            config = await self.db.get_guild_config(interaction.guild_id)
            
            embed = discord.Embed(
                title="Bot Configuration",
                color=discord.Color.blurple()
            )
            
            if config:
                # Status channel
                if config.get('status_channel_id'):
                    channel = self.bot.get_channel(config['status_channel_id'])
                    channel_str = channel.mention if channel else f"Unknown ({config['status_channel_id']})"
                    embed.add_field(
                        name="Status Channel",
                        value=channel_str,
                        inline=True
                    )
                else:
                    embed.add_field(
                        name="Status Channel",
                        value="Not configured",
                        inline=True
                    )
                
                # Notification config
                notif_config = await self.db.get_notification_config(interaction.guild_id)
                if notif_config and notif_config.get('notification_channel_id'):
                    notif_channel = self.bot.get_channel(notif_config['notification_channel_id'])
                    notif_channel_str = notif_channel.mention if notif_channel else "Unknown"
                    
                    notif_role = interaction.guild.get_role(notif_config['notification_role_id']) if notif_config.get('notification_role_id') else None
                    notif_role_str = notif_role.mention if notif_role else "Unknown"
                    
                    emoji = notif_config.get('notification_emoji', '🔔')
                    
                    embed.add_field(
                        name="Notifications",
                        value=f"Channel: {notif_channel_str}\nRole: {notif_role_str}\nEmoji: {emoji}",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="Notifications",
                        value="Not configured",
                        inline=False
                    )
            else:
                embed.description = "No configuration found for this server."
            
            embed.set_footer(text="Use /setup status and /setup notifications to configure")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in setup view: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Failed to retrieve configuration: {str(e)}",
                ephemeral=True
            )


async def setup(bot: commands.Bot) -> None:
    """Setup function for loading cog."""
    pass