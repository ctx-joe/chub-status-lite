"""
Setup commands for bot configuration.
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging

from utils import Database
from cogs.status import StatusCog

logger = logging.getLogger(__name__)


class SetupCog(commands.Cog):
    """Cog for bot setup and configuration commands."""
    
    def __init__(self, bot: commands.Bot, database: Database):
        self.bot = bot
        self.db = database
    
    async def cog_load(self) -> None:
        """Called when cog is loaded."""
        logger.info("Setup cog loaded")
    
    # Create command group
    setup_group = app_commands.Group(
        name="setup",
        description="Bot configuration commands",
        default_permissions=discord.Permissions(administrator=True)
    )
    
    @setup_group.command(name="status", description="Set the channel for status monitoring")
    @app_commands.describe(channel="The channel to post status updates in")
    async def setup_status(
        self, 
        interaction: discord.Interaction, 
        channel: discord.TextChannel
    ) -> None:
        """
        Configure the status monitoring channel.
        
        Creates the initial status embed in the specified channel.
        """
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Get the status cog to initialize the channel
            status_cog: StatusCog = self.bot.get_cog('StatusCog')
            
            if not status_cog:
                await interaction.followup.send(
                    "❌ Status monitoring is not available.",
                    ephemeral=True
                )
                return
            
            # Initialize the status channel
            message = await status_cog.initialize_status_channel(
                guild_id=interaction.guild_id,
                channel=channel
            )
            
            await interaction.followup.send(
                f"✅ Status monitoring configured!\n"
                f"Channel: {channel.mention}\n"
                f"Status message: {message.jump_url}",
                ephemeral=True
            )
            
            logger.info(f"Status channel configured for guild {interaction.guild_id}: {channel.id}")
            
        except Exception as e:
            logger.error(f"Error in setup status: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Failed to configure status channel: {str(e)}",
                ephemeral=True
            )
    
    @setup_group.command(name="view", description="View current bot configuration")
    async def setup_view(self, interaction: discord.Interaction) -> None:
        """Display current configuration for this guild."""
        await interaction.response.defer(ephemeral=True)
        
        try:
            config = await self.db.get_guild_config(interaction.guild_id)
            
            if not config:
                await interaction.followup.send(
                    "No configuration found for this server.\n"
                    "Use `/setup status` to get started.",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title="Bot Configuration",
                color=discord.Color.blue()
            )
            
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
