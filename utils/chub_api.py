"""
Chub.ai API client for status monitoring.
"""

import aiohttp
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

# Chub's model order
MODEL_ORDER = ['asha', 'soji', 'mobile', 'mistral', 'mixtral', 'mythomax']


@dataclass
class ModelStatus:
    """Status information for a single model."""
    name: str
    health: str  # 'green', 'orange', 'red'
    avg_latency: int
    timeout_pct: float  # Timeout as percentage (0.0 - 100.0)
    fail_pct: float     # Failure as percentage (0.0 - 100.0)
    
    @property
    def emoji(self) -> str:
        """Get status emoji."""
        return {'green': '🟢', 'orange': '🟠', 'red': '🔴'}.get(self.health, '⚪')


@dataclass
class ChubStatus:
    """Complete status snapshot from Chub API."""
    timestamp: datetime
    api_health: str
    models: List[ModelStatus]
    raw_data: Dict[str, Any]
    
    @property
    def api_emoji(self) -> str:
        """Get API status emoji."""
        return {'green': '🟢', 'orange': '🟠', 'red': '🔴'}.get(self.api_health, '⚪')
    
    def get_model(self, name: str) -> Optional[ModelStatus]:
        """Get status for a specific model by name."""
        for model in self.models:
            if model.name.lower() == name.lower():
                return model
        return None


class ChubAPIClient:
    """Async client for Chub.ai status API."""
    
    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.session: Optional[aiohttp.ClientSession] = None
        self._last_status: Optional[ChubStatus] = None
        self._last_raw_json: Optional[str] = None
    
    async def start(self) -> None:
        """Initialize the HTTP session."""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
        logger.info(f"Chub API client started: {self.endpoint}")
    
    async def close(self) -> None:
        """Close the HTTP session."""
        if self.session:
            await self.session.close()
            self.session = None
            logger.info("Chub API client closed")
    
    async def fetch_status(self) -> Optional[ChubStatus]:
        """
        Fetch current status from Chub API.
        
        Returns:
            ChubStatus object, or None if fetch failed
        """
        if not self.session:
            logger.error("Session not initialized - call start() first")
            return None
        
        try:
            async with self.session.get(self.endpoint) as response:
                if response.status != 200:
                    logger.warning(f"Chub API returned status {response.status}")
                    return None
                
                data = await response.json()
                return self._parse_status(data)
                
        except aiohttp.ClientError as e:
            logger.error(f"Failed to fetch Chub status: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching status: {e}")
            return None
    
    def _parse_status(self, data: Dict[str, Any]) -> Optional[ChubStatus]:
        """Parse API response into ChubStatus object."""
        try:
            # Get the most recent history entry
            history = data.get('history', [])
            if not history:
                logger.warning("No history in status response")
                return None
            
            latest = history[0]  # Most recent entry
            
            # Parse timestamp
            timestamp_str = latest.get('updated', '')
            try:
                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            except ValueError:
                timestamp = datetime.utcnow()
            
            # Parse API health
            api_health = latest.get('api', 'unknown')
            
            # Parse model statuses in Chub's order
            models = []
            inference = latest.get('inference', {})
            
            for model_name in MODEL_ORDER:
                if model_name in inference:
                    model_data = inference[model_name]
                    if isinstance(model_data, dict):
                        # Handle timeout/fail as floats (they're percentages as decimals)
                        timeout_raw = model_data.get('timeout', 0)
                        fail_raw = model_data.get('fail', 0)
                        
                        # Convert to percentage (0.00740... -> 0.74%)
                        timeout_pct = float(timeout_raw) * 100 if timeout_raw else 0.0
                        fail_pct = float(fail_raw) * 100 if fail_raw else 0.0
                        
                        models.append(ModelStatus(
                            name=model_name,
                            health=model_data.get('health', 'unknown'),
                            avg_latency=int(model_data.get('avg', 0)),
                            timeout_pct=timeout_pct,
                            fail_pct=fail_pct
                        ))
            
            return ChubStatus(
                timestamp=timestamp,
                api_health=api_health,
                models=models,
                raw_data=data
            )
            
        except Exception as e:
            logger.error(f"Failed to parse status response: {e}")
            return None
    
    async def fetch_if_changed(self) -> tuple[Optional[ChubStatus], bool]:
        """
        Fetch status only if it has changed since last fetch.
        
        Returns:
            Tuple of (status, changed) where changed is True if data differs
        """
        if not self.session:
            logger.error("Session not initialized")
            return None, False
        
        try:
            async with self.session.get(self.endpoint) as response:
                if response.status != 200:
                    return None, False
                
                raw_json = await response.text()
                
                # Check if raw response changed
                if raw_json == self._last_raw_json:
                    return self._last_status, False
                
                # Parse new data
                import json
                data = json.loads(raw_json)
                status = self._parse_status(data)
                
                if status:
                    self._last_raw_json = raw_json
                    self._last_status = status
                    return status, True
                
                return None, False
                
        except Exception as e:
            logger.error(f"Error in fetch_if_changed: {e}")
            return None, False
    
    @property
    def last_status(self) -> Optional[ChubStatus]:
        """Get the last successfully fetched status."""
        return self._last_status
