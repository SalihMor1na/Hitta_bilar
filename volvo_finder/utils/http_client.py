"""Async HTTP client using curl_cffi to mimic Chrome TLS fingerprint."""
import asyncio
import json
import logging
from collections import defaultdict
from typing import Any, Optional
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

# Headers that match Chrome 124 — curl_cffi handles TLS fingerprint automatically
_BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


class HttpClient:
    """
    Async HTTP client that impersonates Chrome 124 at the TLS layer.
    Bypasses Cloudflare and similar bot-detection that checks JA3/TLS fingerprints.
    - Per-domain rate limiting
    - Exponential backoff retries (3 attempts)
    - Structured JSON logging
    """

    MAX_RETRIES = 3
    BASE_BACKOFF_SECONDS = 2.0

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._session: Optional[AsyncSession] = None
        self._domain_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def _get_session(self) -> AsyncSession:
        if self._session is None or self._session.closed:
            self._session = AsyncSession(
                impersonate="chrome124",
                headers=_BROWSER_HEADERS,
                timeout=30,
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
        """Fetch URL and return response text."""
        domain = self._get_domain(url)
        session = await self._get_session()

        for attempt in range(self.MAX_RETRIES):
            try:
                await self._rate_limit(domain)
                resp = await session.get(
                    url,
                    params=params,
                    headers=headers,
                    verify=verify_ssl,
                )
                if resp.status_code == 200:
                    logger.debug(json.dumps({"event": "http_success", "url": url}))
                    return resp.text
                elif resp.status_code == 429:
                    wait = self.BASE_BACKOFF_SECONDS * (2 ** attempt)
                    logger.warning(json.dumps({"event": "rate_limited", "url": url, "wait_seconds": wait}))
                    await asyncio.sleep(wait)
                elif resp.status_code in (403, 404):
                    logger.warning(json.dumps({"event": "http_client_error", "url": url, "status": resp.status_code}))
                    return None
                else:
                    logger.warning(json.dumps({
                        "event": "http_error", "url": url,
                        "status": resp.status_code, "attempt": attempt + 1,
                    }))
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(self.BASE_BACKOFF_SECONDS * (2 ** attempt))

            except asyncio.TimeoutError:
                logger.warning(json.dumps({"event": "http_timeout", "url": url, "attempt": attempt + 1}))
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.BASE_BACKOFF_SECONDS * (2 ** attempt))
            except Exception as e:
                logger.warning(json.dumps({"event": "http_client_error", "url": url, "error": str(e), "attempt": attempt + 1}))
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.BASE_BACKOFF_SECONDS * (2 ** attempt))

        logger.error(json.dumps({"event": "http_failed", "url": url, "attempts": self.MAX_RETRIES}))
        return None

    async def get_json(
        self,
        url: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        verify_ssl: bool = True,
    ) -> Optional[Any]:
        """Fetch URL and return parsed JSON."""
        domain = self._get_domain(url)
        session = await self._get_session()
        json_headers = {"Accept": "application/json, text/plain, */*"}
        if headers:
            json_headers.update(headers)

        for attempt in range(self.MAX_RETRIES):
            try:
                await self._rate_limit(domain)
                resp = await session.get(
                    url,
                    params=params,
                    headers=json_headers,
                    verify=verify_ssl,
                )
                if resp.status_code == 200:
                    logger.debug(json.dumps({"event": "json_success", "url": url}))
                    return resp.json()
                elif resp.status_code == 429:
                    wait = self.BASE_BACKOFF_SECONDS * (2 ** attempt)
                    logger.warning(json.dumps({"event": "rate_limited", "url": url, "wait_seconds": wait}))
                    await asyncio.sleep(wait)
                elif resp.status_code in (403, 404):
                    logger.warning(json.dumps({"event": "http_client_error", "url": url, "status": resp.status_code}))
                    return None
                else:
                    logger.warning(json.dumps({
                        "event": "http_unexpected_status", "url": url,
                        "status": resp.status_code, "attempt": attempt + 1,
                    }))
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(self.BASE_BACKOFF_SECONDS * (2 ** attempt))

            except asyncio.TimeoutError:
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.BASE_BACKOFF_SECONDS * (2 ** attempt))
            except Exception as e:
                logger.warning(json.dumps({"event": "json_error", "url": url, "error": str(e), "attempt": attempt + 1}))
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.BASE_BACKOFF_SECONDS * (2 ** attempt))

        return None

    async def warm_up(self, url: str) -> None:
        """Visit a URL to establish session cookies before making other requests."""
        try:
            await self.get_text(url)
        except Exception:
            pass

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "HttpClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
