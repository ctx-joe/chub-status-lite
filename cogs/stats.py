"""
Stats cog - Uptime statistics.
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging
from typing import Optional

from utils import Database

logger = logging.getLogger(__name__)


class StatsCog(commands.Cog):
    """Cog for statistics commands."""
    
    def __init__(self, bot: commands.Bot, database: Database):
        self.bot = bot
        self.db = database
    
    async def cog_load(self) -> None:
        """Called when cog is loaded."""
        logger.info("Stats cog loaded")
    
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
        await interaction.response.defer()
        
        try:
            # Cap days at reasonable maximum
            days = min(days, 30)
            
            if model:
                # Single model stats
                stats = await self.db.get_model_uptime(model, days)
                
                if stats['total'] == 0:
                    await interaction.followup.send(
                        f"No data found for model `{model}` in the last {days} days.",
                        ephemeral=True
                    )
                    return
                
                embed = discord.Embed(
                    title=f"{model.capitalize()} Uptime",
                    description=f"Last {days} days",
                    color=self._uptime_color(stats['green'])
                )
                
                embed.add_field(
                    name="Status Distribution",
                    value=(
                        f"🟢 Healthy: {stats['green']:.1f}%\n"
                        f"🟠 Degraded: {stats['orange']:.1f}%\n"
                        f"🔴 Down: {stats['red']:.1f}%"
                    ),
                    inline=True
                )
                
                embed.add_field(
                    name="Performance",
                    value=f"Avg Latency: {stats['avg_latency']:,}ms",
                    inline=True
                )
                
                embed.set_footer(text=f"Based on {stats['total']} status checks")
                
            else:
                # All models overview
                all_models = await self.db.get_all_models()
                
                if not all_models:
                    await interaction.followup.send(
                        "No uptime data recorded yet.",
                        ephemeral=True
                    )
                    return
                
                embed = discord.Embed(
                    title="Chub.ai Model Uptime",
                    description=f"Last {days} days",
                    color=discord.Color.blue()
                )
                
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
                
                embed.set_footer(text=f"Use /uptime model:<name> for detailed stats")
            
            await interaction.followup.send(embed=embed)
            
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
            return discord.Color.dark_green()
        elif green_pct >= 90:
            return discord.Color.gold()
        elif green_pct >= 80:
            return discord.Color.orange()
        else:
            return discord.Color.red()
    
    def _build_status_bar(self, stats: dict) -> str:
        """Build a visual status bar from stats."""
        total = 10  # 10 segments
        green = int(stats['green'] / 10)
        orange = int(stats['orange'] / 10)
        red = total - green - orange
        
        return '🟢' * green + '🟠' * orange + '🔴' * red
    
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
            f"{emoji} Pong! Latency: `{latency}ms`"
        )


async def setup(bot: commands.Bot) -> None:
    """Setup function for loading cog."""
    pass
