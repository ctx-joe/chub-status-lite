"""
SQLite database wrapper for status logging.
"""

import aiosqlite
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class Database:
    """Async SQLite database for status history and guild config."""
    
    def __init__(self, path: str = "chub_bot.db", retention_days: int = 30):
        self.path = Path(path)
        self.retention_days = retention_days
        self.conn: Optional[aiosqlite.Connection] = None
    
    async def initialize(self) -> None:
        """Initialize database connection and create tables."""
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        
        # Enable WAL mode for better concurrent access
        await self.conn.execute("PRAGMA journal_mode=WAL")
        
        # Create tables
        await self._create_tables()
        await self.conn.commit()
        
        logger.info(f"Database initialized: {self.path}")
    
    async def _create_tables(self) -> None:
        """Create database tables if they don't exist."""
        # Status history for uptime tracking
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS status_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                api_health TEXT NOT NULL,
                model_name TEXT NOT NULL,
                model_health TEXT NOT NULL,
                avg_latency INTEGER,
                timeout_pct REAL DEFAULT 0,
                fail_pct REAL DEFAULT 0
            )
        """)
        
        # Create index for faster queries
        await self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_status_log_timestamp 
            ON status_log(timestamp)
        """)
        
        await self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_status_log_model 
            ON status_log(model_name)
        """)
        
        # Guild configuration
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id INTEGER PRIMARY KEY,
                status_channel_id INTEGER,
                status_message_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
    
    async def close(self) -> None:
        """Close database connection."""
        if self.conn:
            await self.conn.close()
            self.conn = None
            logger.info("Database connection closed")
    
    # --- Status Logging ---
    
    async def log_status(
        self,
        api_health: str,
        model_name: str,
        model_health: str,
        avg_latency: int,
        timeout_pct: float,
        fail_pct: float
    ) -> None:
        """Log a status snapshot for a model."""
        await self.conn.execute(
            """
            INSERT INTO status_log 
            (api_health, model_name, model_health, avg_latency, timeout_pct, fail_pct)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (api_health, model_name, model_health, avg_latency, timeout_pct, fail_pct)
        )
        await self.conn.commit()
    
    async def get_model_uptime(self, model_name: str, days: int = 7) -> Dict[str, Any]:
        """Get uptime statistics for a model over the specified period."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        cursor = await self.conn.execute(
            """
            SELECT 
                model_health,
                COUNT(*) as count,
                AVG(avg_latency) as avg_latency
            FROM status_log
            WHERE model_name = ? AND timestamp > ?
            GROUP BY model_health
            """,
            (model_name, cutoff)
        )
        
        rows = await cursor.fetchall()
        
        # Calculate percentages
        total = sum(row['count'] for row in rows)
        stats = {
            'green': 0.0,
            'orange': 0.0,
            'red': 0.0,
            'total': total,
            'avg_latency': 0
        }
        
        if total > 0:
            for row in rows:
                health = row['model_health']
                if health in stats:
                    stats[health] = (row['count'] / total) * 100
                if row['avg_latency']:
                    stats['avg_latency'] = int(row['avg_latency'])
        
        return stats
    
    async def get_all_models(self) -> List[str]:
        """Get list of all model names in the database."""
        cursor = await self.conn.execute(
            "SELECT DISTINCT model_name FROM status_log ORDER BY model_name"
        )
        rows = await cursor.fetchall()
        return [row['model_name'] for row in rows]
    
    # --- Guild Configuration ---
    
    async def get_guild_config(self, guild_id: int) -> Optional[Dict[str, Any]]:
        """Get configuration for a guild."""
        cursor = await self.conn.execute(
            "SELECT * FROM guild_config WHERE guild_id = ?",
            (guild_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    
    async def set_status_channel(
        self, 
        guild_id: int, 
        channel_id: int,
        message_id: Optional[int] = None
    ) -> None:
        """Set the status monitoring channel for a guild."""
        await self.conn.execute(
            """
            INSERT INTO guild_config (guild_id, status_channel_id, status_message_id, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id) DO UPDATE SET 
                status_channel_id = excluded.status_channel_id,
                status_message_id = excluded.status_message_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (guild_id, channel_id, message_id)
        )
        await self.conn.commit()
    
    async def set_status_message(self, guild_id: int, message_id: int) -> None:
        """Update the status message ID for a guild."""
        await self.conn.execute(
            """
            UPDATE guild_config 
            SET status_message_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE guild_id = ?
            """,
            (message_id, guild_id)
        )
        await self.conn.commit()
    
    async def get_all_status_channels(self) -> List[Dict[str, Any]]:
        """Get all guilds with status channels configured."""
        cursor = await self.conn.execute(
            """
            SELECT guild_id, status_channel_id, status_message_id 
            FROM guild_config 
            WHERE status_channel_id IS NOT NULL
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    
    # --- Maintenance ---
    
    async def daily_maintenance(self) -> Dict[str, int]:
        """Perform daily maintenance tasks."""
        cutoff = datetime.utcnow() - timedelta(days=self.retention_days)
        
        # Delete old status logs
        cursor = await self.conn.execute(
            "DELETE FROM status_log WHERE timestamp < ?",
            (cutoff,)
        )
        status_deleted = cursor.rowcount
        
        # Vacuum to reclaim space
        await self.conn.execute("VACUUM")
        await self.conn.commit()
        
        return {
            'status_deleted': status_deleted
        }
