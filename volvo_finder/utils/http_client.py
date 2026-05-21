"""Async HTTP client with rate limiting, retries, and structured logging."""
import asyncio
import json
import logging
import ssl
from collections import defaultdict
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

# Browser-realistic headers — reduces bot-detection on modern sites
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7",
    # Do NOT set Accept-Encoding manually — let aiohttp manage it.
    # When the 'Brotli' package is installed aiohttp adds 'br' automatically
    # and can decompress it; without the package it omits 'br' to avoid errors.
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

_NO_VERIFY_SSL = ssl.create_default_context()
_NO_VERIFY_SSL.check_hostname = False
_NO_VERIFY_SSL.verify_mode = ssl.CERT_NONE


class HttpClient:
    """
    Async HTTP client with:
    - Per-domain rate limiting
    - Exponential backoff retries (3 attempts)
    - Browser-realistic User-Agent and headers
    - Optional per-request SSL verification bypass
    - Structured JSON logging
    """

    MAX_RETRIES = 3
    BASE_BACKOFF_SECONDS = 2.0

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._session: Optional[aiohttp.ClientSession] = None
        self._domain_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=2)
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers=_BROWSER_HEADERS,
            )
        return self._session

    def _get_domain(self, url: str) -> str:
        return urlparse(url).netloc

    async def _rate_limit(self, domain: str) -> None:
        lock = self._domain_locks[domain]
        async with lock:
            await asyncio.sleep(self.settings.REQUEST_DELAY_SECONDS)

    async def get_text(
        self,
        url: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        verify_ssl: bool = True,
    ) -> Optional[str]:
        """
        Fetch URL and return response text.
        Pass verify_ssl=False for sites with self-signed certificates.
        """
        domain = self._get_domain(url)
        session = await self._get_session()
        ssl_ctx = None if verify_ssl else _NO_VERIFY_SSL

        for attempt in range(self.MAX_RETRIES):
            try:
                await self._rate_limit(domain)
                async with session.get(
                    url, params=params, headers=headers, ssl=ssl_ctx
                ) as response:
                    if response.status == 200:
                        text = await response.text()
                        logger.debug(json.dumps({
                            "event": "http_success",
                            "url": url,
                            "status": response.status,
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

    async def get_json(
        self,
        url: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        verify_ssl: bool = True,
    ) -> Optional[Any]:
        """
        Fetch URL and return parsed JSON.
        Pass verify_ssl=False for sites with self-signed certificates.
        """
        domain = self._get_domain(url)
        session = await self._get_session()
        ssl_ctx = None if verify_ssl else _NO_VERIFY_SSL
        json_headers = {"Accept": "application/json, text/plain, */*"}
        if headers:
            json_headers.update(headers)

        for attempt in range(self.MAX_RETRIES):
            try:
                await self._rate_limit(domain)
                async with session.get(
                    url, params=params, headers=json_headers, ssl=ssl_ctx
                ) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        logger.debug(json.dumps({
                            "event": "json_success",
                            "url": url,
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
                        logger.warning(json.dumps({
                            "event": "http_unexpected_status",
                            "url": url,
                            "status": response.status,
                            "attempt": attempt + 1,
                        }))
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
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "HttpClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
