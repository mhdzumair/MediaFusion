import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from db.config import settings
from db.redis_database import REDIS_SYNC_CLIENT

logger = logging.getLogger(__name__)


class RemoteConfigManager:
    CACHE_KEY = "scraper_config"
    CACHE_TTL = 3600  # Cache for 1 hour

    def __init__(self):
        self.redis_client = REDIS_SYNC_CLIENT

    def get_config(self) -> Dict[str, Any]:
        """
        Retrieve the configuration, first checking Redis cache,
        then fetching from remote or local source if necessary.
        """
        cached_config = self._get_cached_config()
        if cached_config:
            return cached_config

        try:
            config = self._fetch_config()
            self._cache_config(config)
            return config
        except Exception as e:
            logger.error(f"Error fetching config: {e}")
            return self._load_local_fallback()

    def _get_cached_config(self) -> Optional[Dict[str, Any]]:
        """Retrieve the configuration from Redis cache if available."""
        cached_config = self.redis_client.get(self.CACHE_KEY)
        if cached_config:
            return json.loads(cached_config)
        return None

    def _fetch_config(self) -> Dict[str, Any]:
        """Fetch the configuration from either remote or local source."""
        if settings.use_config_source == "local":
            return self._load_local_config(settings.local_config_path)
        if settings.remote_config_source.startswith(("http://", "https://")):
            return self._fetch_remote_config(settings.remote_config_source)
        return self._load_local_config(settings.remote_config_source)

    def _cache_config(self, config: Dict[str, Any]) -> None:
        """Cache the configuration in Redis."""
        self.redis_client.setex(self.CACHE_KEY, self.CACHE_TTL, json.dumps(config))

    @staticmethod
    def _fetch_remote_config(config_url: str) -> Dict[str, Any]:
        """Fetch the configuration from a remote URL."""
        try:
            response = httpx.get(config_url, timeout=10)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, httpx.RequestError) as e:
            logger.error(f"HTTP error occurred while fetching remote config: {e}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in remote config: {e}")
            raise

    @staticmethod
    def _load_local_config(config_path: str) -> Dict[str, Any]:
        """Load the configuration from a local file."""
        path = Path(config_path)
        if not path.is_file():
            raise FileNotFoundError(f"Config file not found: {path}")
        try:
            with path.open("r") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in local config file: {e}")
            raise

    def _load_local_fallback(self) -> Dict[str, Any]:
        """Load the local fallback configuration."""
        try:
            return self._load_local_config(settings.local_config_path)
        except Exception as e:
            logger.error(f"Failed to load local fallback config: {e}")
            return {}

    def get_start_url(self, spider_name: str) -> Optional[str]:
        """Get the start URL for a specific spider."""
        config = self.get_config()
        return config.get("start_urls", {}).get(spider_name)

    def get_scraper_config(self, site_name: str, key: str) -> Any:
        """Get a specific configuration for a scraper."""
        config = self.get_config()
        return config.get(site_name, {}).get(key)


config_manager = RemoteConfigManager()
