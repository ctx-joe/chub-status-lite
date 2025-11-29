"""
SQLite database wrapper with single connection and async support.
"""

import aiosqlite
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class Database:
    """Async SQLite database with single persistent connection."""
    
    def __init__(self, path: str, retention_days: int = 30):
        self.path = Path(path)
        self.retention_days = retention_days
        self.conn: Optional[aiosqlite.Connection] = None
    
    async def initialize(self) -> None:
        """Initialize database connection and create tables."""
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        
        # Enable WAL mode for better concurrent read performance
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA busy_timeout=5000")
        
        await self._create_tables()
        logger.info(f"Database connected: {self.path}")
    
    async def close(self) -> None:
        """Close database connection."""
        if self.conn:
            await self.conn.close()
            self.conn = None
            logger.info("Database connection closed")
    
    async def _create_tables(self) -> None:
        """Create database schema."""
        
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
        
        # Index for faster time-based queries
        await self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_status_log_timestamp 
            ON status_log(timestamp)
        """)
        
        await self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_status_log_model 
            ON status_log(model_name, timestamp)
        """)
        
        # Guild configuration
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id INTEGER PRIMARY KEY,
                status_channel_id INTEGER,
                status_message_id INTEGER,
                notification_channel_id INTEGER,
                notification_role_id INTEGER,
                notification_emoji TEXT DEFAULT '🔔',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Alert state tracking for notifications
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_state (
                guild_id INTEGER,
                model_name TEXT,
                consecutive_red INTEGER DEFAULT 0,
                consecutive_green INTEGER DEFAULT 0,
                is_notified BOOLEAN DEFAULT FALSE,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, model_name)
            )
        """)
        
        # Migration: add notification columns if they don't exist
        try:
            await self.conn.execute("ALTER TABLE guild_config ADD COLUMN notification_channel_id INTEGER")
        except:
            pass  # Column already exists
        try:
            await self.conn.execute("ALTER TABLE guild_config ADD COLUMN notification_role_id INTEGER")
        except:
            pass
        try:
            await self.conn.execute("ALTER TABLE guild_config ADD COLUMN notification_emoji TEXT DEFAULT '🔔'")
        except:
            pass
        
        await self.conn.commit()
        logger.info("Database tables initialized")
    
    # --- Status Log Methods ---
    
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
    
    async def get_model_uptime(
        self, 
        model_name: str, 
        days: int = 7
    ) -> Dict[str, Any]:
        """Get uptime statistics for a model over the specified period."""
        cursor = await self.conn.execute(
            """
            SELECT 
                model_health,
                COUNT(*) as count,
                AVG(avg_latency) as avg_latency
            FROM status_log
            WHERE model_name = ?
              AND timestamp >= datetime('now', ?)
            GROUP BY model_health
            """,
            (model_name, f'-{days} days')
        )
        rows = await cursor.fetchall()
        
        total = sum(row['count'] for row in rows)
        if total == 0:
            return {'green': 0, 'yellow': 0, 'orange': 0, 'red': 0, 'avg_latency': 0, 'total': 0}
        
        stats = {'green': 0, 'yellow': 0, 'orange': 0, 'red': 0, 'total': total}
        total_latency = 0
        latency_count = 0
        
        for row in rows:
            health = row['model_health']
            count = row['count']
            if health in stats:
                stats[health] = round((count / total) * 100, 1)
            if row['avg_latency']:
                total_latency += row['avg_latency'] * count
                latency_count += count
        
        stats['avg_latency'] = int(total_latency / latency_count) if latency_count else 0
        return stats
    
    async def get_all_models(self) -> List[str]:
        """Get list of all tracked models."""
        cursor = await self.conn.execute(
            "SELECT DISTINCT model_name FROM status_log ORDER BY model_name"
        )
        rows = await cursor.fetchall()
        return [row['model_name'] for row in rows]
    
    # --- Guild Config Methods ---
    
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
        """Set the status channel for a guild."""
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
        """Update just the message ID for a guild's status embed."""
        await self.conn.execute(
            """
            UPDATE guild_config 
            SET status_message_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE guild_id = ?
            """,
            (message_id, guild_id)
        )
        await self.conn.commit()
    
    async def get_all_status_channels(self) -> List[Dict[str, int]]:
        """Get all configured status channels across guilds."""
        cursor = await self.conn.execute(
            """
            SELECT guild_id, status_channel_id, status_message_id 
            FROM guild_config 
            WHERE status_channel_id IS NOT NULL
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    
    # --- Notification Config Methods ---
    
    async def set_notification_config(
        self,
        guild_id: int,
        channel_id: int,
        role_id: int,
        emoji: str = '🔔'
    ) -> None:
        """Set notification configuration for a guild."""
        await self.conn.execute(
            """
            INSERT INTO guild_config (guild_id, notification_channel_id, notification_role_id, notification_emoji, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id) DO UPDATE SET 
                notification_channel_id = excluded.notification_channel_id,
                notification_role_id = excluded.notification_role_id,
                notification_emoji = excluded.notification_emoji,
                updated_at = CURRENT_TIMESTAMP
            """,
            (guild_id, channel_id, role_id, emoji)
        )
        await self.conn.commit()
    
    async def clear_notification_config(self, guild_id: int) -> None:
        """Clear notification configuration for a guild."""
        await self.conn.execute(
            """
            UPDATE guild_config 
            SET notification_channel_id = NULL, 
                notification_role_id = NULL, 
                notification_emoji = '🔔',
                updated_at = CURRENT_TIMESTAMP
            WHERE guild_id = ?
            """,
            (guild_id,)
        )
        await self.conn.commit()
    
    async def get_notification_config(self, guild_id: int) -> Optional[Dict[str, Any]]:
        """Get notification configuration for a guild."""
        cursor = await self.conn.execute(
            """
            SELECT notification_channel_id, notification_role_id, notification_emoji,
                   status_channel_id, status_message_id
            FROM guild_config 
            WHERE guild_id = ?
            """,
            (guild_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    
    async def get_all_notification_configs(self) -> List[Dict[str, Any]]:
        """Get all guilds with notification configured."""
        cursor = await self.conn.execute(
            """
            SELECT guild_id, notification_channel_id, notification_role_id, notification_emoji,
                   status_channel_id, status_message_id
            FROM guild_config 
            WHERE notification_channel_id IS NOT NULL
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    
    # --- Alert State Methods ---
    
    async def get_alert_state(self, guild_id: int, model_name: str) -> Optional[Dict[str, Any]]:
        """Get alert state for a model in a guild."""
        cursor = await self.conn.execute(
            """
            SELECT consecutive_red, consecutive_green, is_notified, updated_at
            FROM alert_state
            WHERE guild_id = ? AND model_name = ?
            """,
            (guild_id, model_name)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    
    async def update_alert_state(
        self,
        guild_id: int,
        model_name: str,
        consecutive_red: int,
        consecutive_green: int,
        is_notified: bool
    ) -> None:
        """Update alert state for a model in a guild."""
        await self.conn.execute(
            """
            INSERT INTO alert_state (guild_id, model_name, consecutive_red, consecutive_green, is_notified, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id, model_name) DO UPDATE SET
                consecutive_red = excluded.consecutive_red,
                consecutive_green = excluded.consecutive_green,
                is_notified = excluded.is_notified,
                updated_at = CURRENT_TIMESTAMP
            """,
            (guild_id, model_name, consecutive_red, consecutive_green, is_notified)
        )
        await self.conn.commit()
    
    async def get_all_alert_states(self, guild_id: int) -> Dict[str, Dict[str, Any]]:
        """Get all alert states for a guild, keyed by model name."""
        cursor = await self.conn.execute(
            """
            SELECT model_name, consecutive_red, consecutive_green, is_notified
            FROM alert_state
            WHERE guild_id = ?
            """,
            (guild_id,)
        )
        rows = await cursor.fetchall()
        return {row['model_name']: dict(row) for row in rows}
    
    async def clear_alert_states(self, guild_id: int) -> None:
        """Clear all alert states for a guild."""
        await self.conn.execute(
            "DELETE FROM alert_state WHERE guild_id = ?",
            (guild_id,)
        )
        await self.conn.commit()
    
    # --- Status History Methods (for loading on restart) ---
    
    async def get_recent_model_health(self, model_name: str, limit: int = 10) -> List[str]:
        """Get recent health values for a model (newest first, reversed for deque)."""
        cursor = await self.conn.execute(
            """
            SELECT model_health
            FROM status_log
            WHERE model_name = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (model_name, limit)
        )
        rows = await cursor.fetchall()
        # Reverse so oldest is first (matches deque append order)
        return [row['model_health'] for row in reversed(rows)]
    
    # --- Maintenance Methods ---
    
    async def daily_maintenance(self) -> Dict[str, int]:
        """Purge old logs and vacuum database. Returns count of deleted rows."""
        
        # Delete old status logs
        cursor = await self.conn.execute(
            f"""
            DELETE FROM status_log 
            WHERE timestamp < datetime('now', '-{self.retention_days} days')
            """
        )
        status_deleted = cursor.rowcount
        
        await self.conn.commit()
        
        # Vacuum to reclaim space
        await self.conn.execute("VACUUM")
        
        logger.info(f"Daily maintenance complete: deleted {status_deleted} status logs")
        
        return {'status_deleted': status_deleted}