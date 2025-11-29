"""
Chub Status Lite - Discord bot for monitoring Chub.ai service status.

A lightweight status monitoring bot with outage notifications.
"""

import discord
from discord.ext import commands
import yaml
import logging
import asyncio
from pathlib import Path

from utils import ChubAPIClient, Database
from cogs.status import StatusCog
from cogs.setup import SetupCog
from cogs.stats import StatsCog

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def load_config(path: str = "config.yaml") -> dict:
    """Load configuration from YAML file."""
    with open(path, 'r') as f:
        return yaml.safe_load(f)


async def main():
    """Main entry point."""
    config = load_config()
    
    # Set up intents
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.members = True  # Required for role management
    
    # Create bot instance
    bot = commands.Bot(
        command_prefix="!",  # Prefix commands not used, but required
        intents=intents,
        help_command=None
    )
    
    # Initialize shared resources
    database = Database(config['database']['path'])
    chub_client = ChubAPIClient(config['status']['endpoint'])
    
    @bot.event
    async def on_ready():
        """Called when bot is ready."""
        logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
        logger.info(f"Connected to {len(bot.guilds)} guilds")
        
        # Sync slash commands
        try:
            # Sync to specific guilds if configured
            allowed_guilds = config['discord'].get('allowed_guilds', [])
            if allowed_guilds:
                for guild_id in allowed_guilds:
                    guild = discord.Object(id=guild_id)
                    bot.tree.copy_global_to(guild=guild)
                    await bot.tree.sync(guild=guild)
                logger.info(f"Synced commands to {len(allowed_guilds)} guilds")
            else:
                await bot.tree.sync()
                logger.info("Synced commands globally")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")
    
    # Initialize database
    await database.initialize()
    
    # Add cogs
    status_cog = StatusCog(
        bot=bot,
        chub_client=chub_client,
        database=database,
        poll_interval=config['status'].get('poll_interval_seconds', 60),
        history_depth=config['status'].get('history_depth', 10)
    )
    
    setup_cog = SetupCog(bot=bot, database=database)
    stats_cog = StatsCog(bot=bot, database=database)
    
    await bot.add_cog(status_cog)
    await bot.add_cog(setup_cog)
    await bot.add_cog(stats_cog)
    
    logger.info("All cogs loaded")
    
    # Run the bot
    try:
        await bot.start(config['discord']['token'])
    finally:
        await database.close()
        await chub_client.close()


if __name__ == "__main__":
    asyncio.run(main())