"""
Statistics and utility commands cog.
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging
from typing import Optional

from utils import Database

logger = logging.getLogger(__name__)

# Model order for display consistency
MODEL_ORDER = ['asha', 'soji', 'mobile', 'mistral', 'mixtral', 'mythomax']


class StatsCog(commands.Cog):
    """Cog for statistics and utility commands."""
    
    def __init__(self, bot: commands.Bot, database: Database):
        self.bot = bot
        self.db = database
    
    @app_commands.command(name="uptime", description="Show uptime statistics for Chub.ai models")
    @app_commands.describe(
        model="Specific model to check (leave empty for all)",
        days="Number of days to look back (default: 7)"
    )
    async def uptime(
        self, 
        interaction: discord.Interaction, 
        model: Optional[str] = None,
        days: int = 7
    ) -> None:
        """
        Display uptime statistics for models.
        
        Shows percentage healthy/degraded/down over the specified period.
        """
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Validate days parameter
            if days < 1:
                days = 1
            elif days > 90:
                days = 90
            
            embed = discord.Embed(
                title=f"📊 Uptime Statistics ({days} days)",
                color=discord.Color.blurple()
            )
            
            if model:
                # Single model detailed view
                model = model.lower()
                stats = await self.db.get_model_uptime(model, days)
                
                if stats['total'] == 0:
                    await interaction.followup.send(
                        f"No data found for model: {model}",
                        ephemeral=True
                    )
                    return
                
                embed.title = f"📊 {model.capitalize()} Uptime ({days} days)"
                embed.color = self._uptime_color(stats['green'])
                
                # Build status breakdown
                status_lines = []
                status_lines.append(f"🟢 Healthy: **{stats['green']}%**")
                if stats.get('yellow', 0) > 0:
                    status_lines.append(f"🟡 Warning: **{stats['yellow']}%**")
                if stats.get('orange', 0) > 0:
                    status_lines.append(f"🟠 Degraded: **{stats['orange']}%**")
                if stats['red'] > 0:
                    status_lines.append(f"🔴 Down: **{stats['red']}%**")
                
                embed.add_field(
                    name="Status Breakdown",
                    value="\n".join(status_lines),
                    inline=False
                )
                
                embed.add_field(
                    name="Average Latency",
                    value=f"`{stats['avg_latency']:,}ms`",
                    inline=True
                )
                
                embed.add_field(
                    name="Data Points",
                    value=f"`{stats['total']:,}`",
                    inline=True
                )
            else:
                # Overview of all models
                all_models = MODEL_ORDER
                
                for model_name in all_models:
                    stats = await self.db.get_model_uptime(model_name, days)
                    if stats['total'] > 0:
                        # Compact format for overview
                        status_bar = self._build_status_bar(stats)
                        embed.add_field(
                            name=model_name.capitalize(),
                            value=f"{status_bar}\n`{stats['avg_latency']:,}ms avg`",
                            inline=True
                        )
                
                embed.set_footer(text="Use /uptime model:<n> for detailed stats")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in /uptime command: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ An error occurred: {str(e)}",
                ephemeral=True
            )
    
    def _uptime_color(self, green_pct: float) -> discord.Color:
        """Get color based on uptime percentage."""
        if green_pct >= 99:
            return discord.Color.green()
        elif green_pct >= 95:
            return discord.Color.gold()
        elif green_pct >= 90:
            return discord.Color.orange()
        else:
            return discord.Color.red()
    
    def _build_status_bar(self, stats: dict) -> str:
        """Build a compact status bar showing uptime distribution."""
        green_pct = stats['green']
        
        # Simple percentage display
        if green_pct >= 99:
            emoji = "🟢"
        elif green_pct >= 95:
            emoji = "🟡"
        elif green_pct >= 90:
            emoji = "🟠"
        else:
            emoji = "🔴"
        
        return f"{emoji} **{green_pct}%** healthy"
    
    @app_commands.command(name="ping", description="Check bot latency")
    async def ping(self, interaction: discord.Interaction) -> None:
        """Simple ping command to check bot health."""
        latency = round(self.bot.latency * 1000)
        
        if latency < 100:
            emoji = "🟢"
        elif latency < 200:
            emoji = "🟡"
        else:
            emoji = "🔴"
        
        await interaction.response.send_message(
            f"{emoji} Pong! Latency: `{latency}ms`",
            ephemeral=True
        )
    
    @app_commands.command(name="help", description="Show bot commands and information")
    async def help_command(self, interaction: discord.Interaction) -> None:
        """Display help information about the bot."""
        embed = discord.Embed(
            title="Chub Status Lite",
            color=discord.Color.blurple()
        )
        
        embed.add_field(
            name="Status Monitoring",
            value=(
                "• Status embed updates automatically every 60 seconds\n"
                "• Visual history shows last 10 status checks (← older | newer →)\n"
                "• 🟢 Healthy | 🟡 Warning | 🔴 Degraded/Down"
            ),
            inline=False
        )
        
        embed.add_field(
            name="Commands",
            value=(
                "`/uptime [model] [days]` - View uptime statistics\n"
                "`/ping` - Check bot latency\n"
                "`/help` - Show this message"
            ),
            inline=False
        )
        
        embed.add_field(
            name="Admin Commands",
            value=(
                "`/setup status #channel` - Set status embed channel\n"
                "`/setup notifications #channel @role [emoji]` - Set alert channel and role\n"
                "`/setup view` - View current configuration"
            ),
            inline=False
        )
        
        embed.add_field(
            name="Notifications",
            value=(
                "• React with the subscription emoji on the status embed to receive outage alerts\n"
                "• Alerts trigger after ~10 minutes of confirmed downtime\n"
                "• Recovery notifications after ~15 minutes of stable service"
            ),
            inline=False
        )
        
        embed.set_footer(text="Chub Status Lite")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Setup function for loading cog."""
    pass