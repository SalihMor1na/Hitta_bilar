"""File-based HTTP response cache with TTL support."""
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)


class Cache:
    """
    File-based cache stored in CACHE_DIR.

    - Cache key = MD5 of URL
    - Each entry stored as a JSON file: {content, timestamp}
    - TTL from settings (default 6 hours)
    """

    def __init__(self, settings: Any) -> None:
        self.cache_dir = settings.CACHE_DIR
        self.ttl_hours = settings.CACHE_TTL_HOURS
        os.makedirs(self.cache_dir, exist_ok=True)

    def _cache_key(self, url: str) -> str:
        return hashlib.md5(url.encode("utf-8")).hexdigest()

    def _cache_path(self, url: str) -> str:
        key = self._cache_key(url)
        return os.path.join(self.cache_dir, f"{key}.json")

    def get(self, url: str) -> Optional[str]:
        """
        Return cached content for URL if it exists and is within TTL.
        Returns None on cache miss or expired entry.
        """
        path = self._cache_path(url)
        if not os.path.exists(path):
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            cached_at = datetime.fromisoformat(data["timestamp"])
            expiry = cached_at + timedelta(hours=self.ttl_hours)

            if datetime.utcnow() > expiry:
                logger.debug(f"Cache expired for {url}")
                os.remove(path)
                return None

            logger.debug(f"Cache hit for {url}")
            return data["content"]

        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning(f"Cache read error for {url}: {e}")
            return None

    def set(self, url: str, content: str) -> None:
        """Store content in cache with current timestamp."""
        path = self._cache_path(url)
        data = {
            "url": url,
            "content": content,
            "timestamp": datetime.utcnow().isoformat(),
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            logger.debug(f"Cached {url}")
        except OSError as e:
            logger.warning(f"Cache write error for {url}: {e}")

    def invalidate(self, url: str) -> None:
        """Remove a specific URL from cache."""
        path = self._cache_path(url)
        if os.path.exists(path):
            os.remove(path)

    def clear(self) -> None:
        """Remove all cached entries."""
        for filename in os.listdir(self.cache_dir):
            if filename.endswith(".json"):
                os.remove(os.path.join(self.cache_dir, filename))
        logger.info("Cache cleared")
