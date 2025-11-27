"""
Chub Status Bot Lite - Status monitoring only.

A lightweight Discord bot for monitoring Chub.ai service status.
No LLM capabilities - just status and stats.

Run with: python bot.py
"""

import asyncio
import logging
import sys
from pathlib import Path

import discord
from discord.ext import commands, tasks
import yaml

from utils import Database, ChubAPIClient
from cogs import StatusCog, StatsCog, SetupCog

# Get script directory for relative paths
SCRIPT_DIR = Path(__file__).parent.resolve()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(SCRIPT_DIR / 'bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger('chub_bot')


def load_config(path: str = "config.yaml") -> dict:
    """Load configuration from YAML file."""
    config_path = SCRIPT_DIR / path
    
    if not config_path.exists():
        config_path = Path(path)
    
    if not config_path.exists():
        logger.error(f"Config file not found: {path}")
        logger.info(f"Looked in: {SCRIPT_DIR}")
        logger.info("Please copy config.yaml.example to config.yaml and fill in your values.")
        sys.exit(1)
    
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    # Validate required fields
    if not config.get('discord', {}).get('token'):
        logger.error("Missing required config: discord.token")
        sys.exit(1)
    
    return config


class ChubBotLite(commands.Bot):
    """Lightweight bot for status monitoring only."""
    
    def __init__(self, config: dict):
        self.config = config
        
        # Set up intents
        intents = discord.Intents.default()
        intents.guilds = True
        
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
        
        # Guild whitelist (empty = allow all)
        self.allowed_guilds = set(config.get('discord', {}).get('allowed_guilds', []) or [])
        
        # Initialize components
        self.database: Database = None
        self.chub_client: ChubAPIClient = None
    
    async def setup_hook(self) -> None:
        """Called when bot is starting up."""
        logger.info("Initializing bot components...")
        
        if self.allowed_guilds:
            logger.info(f"Guild whitelist enabled: {len(self.allowed_guilds)} guilds")
        else:
            logger.info("Guild whitelist disabled (allowing all guilds)")
        
        # Initialize database
        db_config = self.config.get('database', {})
        db_path = SCRIPT_DIR / db_config.get('path', 'chub_bot.db')
        self.database = Database(
            path=str(db_path),
            retention_days=db_config.get('retention_days', 30)
        )
        await self.database.initialize()
        logger.info("Database initialized")
        
        # Initialize Chub API client
        status_config = self.config.get('status', {})
        self.chub_client = ChubAPIClient(
            endpoint=status_config.get('endpoint', 'https://gateway.chub.ai/monitoring/health/public/status')
        )
        logger.info("Chub API client initialized")
        
        # Load cogs
        await self._load_cogs()
        
        # Start maintenance task
        self.daily_maintenance.start()
    
    async def _load_cogs(self) -> None:
        """Load all cogs."""
        status_config = self.config.get('status', {})
        
        # Status monitoring
        status_cog = StatusCog(
            bot=self,
            chub_client=self.chub_client,
            database=self.database,
            poll_interval=status_config.get('poll_interval_seconds', 10),
            history_depth=status_config.get('history_depth', 10)
        )
        await self.add_cog(status_cog)
        
        # Stats commands
        stats_cog = StatsCog(bot=self, database=self.database)
        await self.add_cog(stats_cog)
        
        # Setup commands
        setup_cog = SetupCog(bot=self, database=self.database)
        await self.add_cog(setup_cog)
        
        logger.info(f"Loaded {len(self.cogs)} cogs")
    
    def is_guild_allowed(self, guild_id: int) -> bool:
        """Check if a guild is in the whitelist."""
        if not self.allowed_guilds:
            return True
        return guild_id in self.allowed_guilds
    
    async def on_ready(self) -> None:
        """Called when bot is fully connected."""
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guilds")
        
        if self.allowed_guilds:
            for guild in self.guilds:
                status = "✓ allowed" if self.is_guild_allowed(guild.id) else "✗ blocked"
                logger.info(f"  Guild: {guild.name} ({guild.id}) - {status}")
        
        # Sync slash commands
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} slash commands")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")
    
    @tasks.loop(hours=24)
    async def daily_maintenance(self) -> None:
        """Daily database maintenance."""
        if self.database is None:
            logger.debug("Database not initialized yet, skipping maintenance")
            return
        
        try:
            results = await self.database.daily_maintenance()
            logger.info(f"Daily maintenance: deleted {results['status_deleted']} status logs")
        except Exception as e:
            logger.error(f"Daily maintenance failed: {e}")
    
    @daily_maintenance.before_loop
    async def before_daily_maintenance(self) -> None:
        """Wait for bot to be ready."""
        await self.wait_until_ready()
    
    async def close(self) -> None:
        """Clean shutdown."""
        logger.info("Shutting down...")
        
        self.daily_maintenance.cancel()
        
        if self.chub_client:
            await self.chub_client.close()
        
        if self.database:
            await self.database.close()
        
        await super().close()
        logger.info("Shutdown complete")


async def main() -> None:
    """Main entry point."""
    config = load_config()
    bot = ChubBotLite(config)
    
    try:
        await bot.start(config['discord']['token'])
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
