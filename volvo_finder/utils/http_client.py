"""Async HTTP client with rate limiting, retries, and structured logging."""
import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)


class HttpClient:
    """
    Async HTTP client with:
    - Per-domain rate limiting
    - Exponential backoff retries (3 attempts)
    - Configurable User-Agent
    - Structured JSON logging
    """

    MAX_RETRIES = 3
    BASE_BACKOFF_SECONDS = 2.0

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._session: Optional[aiohttp.ClientSession] = None
        self._domain_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._headers = {
            "User-Agent": settings.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=2)
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers=self._headers,
            )
        return self._session

    def _get_domain(self, url: str) -> str:
        parsed = urlparse(url)
        return parsed.netloc

    async def _rate_limit(self, domain: str) -> None:
        """Enforce per-domain rate limiting."""
        lock = self._domain_locks[domain]
        async with lock:
            await asyncio.sleep(self.settings.REQUEST_DELAY_SECONDS)

    async def get_text(self, url: str, params: Optional[dict] = None) -> Optional[str]:
        """
        Fetch URL and return response text.
        Retries up to MAX_RETRIES times with exponential backoff.
        Returns None on failure.
        """
        domain = self._get_domain(url)
        session = await self._get_session()

        for attempt in range(self.MAX_RETRIES):
            try:
                await self._rate_limit(domain)
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        text = await response.text()
                        logger.debug(json.dumps({
                            "event": "http_success",
                            "url": url,
                            "status": response.status,
                            "attempt": attempt + 1,
                        }))
                        return text
                    elif response.status == 429:
                        wait = self.BASE_BACKOFF_SECONDS * (2 ** attempt)
                        logger.warning(json.dumps({
                            "event": "rate_limited",
                            "url": url,
                            "wait_seconds": wait,
                        }))
                        await asyncio.sleep(wait)
                    elif response.status in (403, 404):
                        logger.warning(json.dumps({
                            "event": "http_client_error",
                            "url": url,
                            "status": response.status,
                        }))
                        return None
                    else:
                        logger.warning(json.dumps({
                            "event": "http_error",
                            "url": url,
                            "status": response.status,
                            "attempt": attempt + 1,
                        }))
                        if attempt < self.MAX_RETRIES - 1:
                            await asyncio.sleep(self.BASE_BACKOFF_SECONDS * (2 ** attempt))

            except asyncio.TimeoutError:
                logger.warning(json.dumps({
                    "event": "http_timeout",
                    "url": url,
                    "attempt": attempt + 1,
                }))
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.BASE_BACKOFF_SECONDS * (2 ** attempt))
            except aiohttp.ClientError as e:
                logger.warning(json.dumps({
                    "event": "http_client_error",
                    "url": url,
                    "error": str(e),
                    "attempt": attempt + 1,
                }))
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.BASE_BACKOFF_SECONDS * (2 ** attempt))

        logger.error(json.dumps({
            "event": "http_failed",
            "url": url,
            "attempts": self.MAX_RETRIES,
        }))
        return None

    async def get_json(self, url: str, params: Optional[dict] = None) -> Optional[Any]:
        """
        Fetch URL and return parsed JSON.
        Returns None on failure.
        """
        domain = self._get_domain(url)
        session = await self._get_session()

        for attempt in range(self.MAX_RETRIES):
            try:
                await self._rate_limit(domain)
                headers = {**self._headers, "Accept": "application/json"}
                async with session.get(url, params=params, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        logger.debug(json.dumps({
                            "event": "json_success",
                            "url": url,
                            "attempt": attempt + 1,
                        }))
                        return data
                    elif response.status == 429:
                        wait = self.BASE_BACKOFF_SECONDS * (2 ** attempt)
                        logger.warning(json.dumps({
                            "event": "rate_limited",
                            "url": url,
                            "wait_seconds": wait,
                        }))
                        await asyncio.sleep(wait)
                    elif response.status in (403, 404):
                        logger.warning(json.dumps({
                            "event": "http_client_error",
                            "url": url,
                            "status": response.status,
                        }))
                        return None
                    else:
                        if attempt < self.MAX_RETRIES - 1:
                            await asyncio.sleep(self.BASE_BACKOFF_SECONDS * (2 ** attempt))

            except asyncio.TimeoutError:
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.BASE_BACKOFF_SECONDS * (2 ** attempt))
            except (aiohttp.ClientError, json.JSONDecodeError, Exception) as e:
                logger.warning(json.dumps({
                    "event": "json_error",
                    "url": url,
                    "error": str(e),
                    "attempt": attempt + 1,
                }))
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.BASE_BACKOFF_SECONDS * (2 ** attempt))

        return None

    async def close(self) -> None:
        """Close the underlying aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "HttpClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
