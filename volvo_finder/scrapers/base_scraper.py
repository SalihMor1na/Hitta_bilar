"""Abstract base class for all car scrapers."""
from abc import ABC, abstractmethod
import asyncio
import json
import logging
import os
from datetime import datetime
from urllib.robotparser import RobotFileParser
from urllib.parse import urljoin

from utils.http_client import HttpClient
from utils.cache import Cache
from models.car import Car

RESTRICTED_SOURCES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "RESTRICTED_SOURCES.md",
)


class BaseScraper(ABC):
    """Abstract base scraper. All site scrapers inherit from this."""

    NAME: str = ""
    BASE_URL: str = ""

    def __init__(self, http_client: HttpClient, cache: Cache) -> None:
        self.http_client = http_client
        self.cache = cache
        self.logger = logging.getLogger(f"scraper.{self.NAME}")

    async def check_robots_txt(self) -> bool:
        """
        Fetch and parse robots.txt for BASE_URL.
        Returns True if scraping is allowed; logs to RESTRICTED_SOURCES.md if not.
        """
        robots_url = urljoin(self.BASE_URL, "/robots.txt")
        try:
            text = await self.http_client.get_text(robots_url)
            if text is None:
                # Cannot fetch robots.txt — assume allowed (fail open)
                return True

            parser = RobotFileParser()
            parser.parse(text.splitlines())

            user_agent = "VolvoFinder"
            allowed = parser.can_fetch(user_agent, self.BASE_URL)
            if not allowed:
                # Also check generic *
                allowed = parser.can_fetch("*", self.BASE_URL)

            if not allowed:
                self.logger.warning(json.dumps({
                    "event": "robots_restricted",
                    "scraper": self.NAME,
                    "url": self.BASE_URL,
                }))
                self._log_restricted_source()
                return False

            return True

        except Exception as e:
            self.logger.warning(json.dumps({
                "event": "robots_check_failed",
                "scraper": self.NAME,
                "error": str(e),
            }))
            # Fail open — attempt scrape anyway
            return True

    def _log_restricted_source(self) -> None:
        """Append this source to RESTRICTED_SOURCES.md."""
        try:
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
            row = f"| {self.NAME} | {self.BASE_URL} | {date_str} | robots.txt disallows scraping |\n"
            with open(RESTRICTED_SOURCES_FILE, "a", encoding="utf-8") as f:
                f.write(row)
        except OSError:
            pass

    @abstractmethod
    async def fetch(self) -> list[dict]:
        """Fetch raw data from source. Returns list of raw dicts."""
        ...

    @abstractmethod
    def parse(self, raw_data: list[dict]) -> list[dict]:
        """Parse raw data into intermediate dicts."""
        ...

    @abstractmethod
    def normalize(self, parsed: list[dict]) -> list[Car]:
        """Normalize intermediate dicts to Car dataclass instances."""
        ...

    async def scrape(self) -> list[Car]:
        """
        Main entry point: check robots, fetch, parse, normalize.
        Per-scraper try/except — never propagates exceptions.
        """
        try:
            allowed = await self.check_robots_txt()
            if not allowed:
                self.logger.info(json.dumps({
                    "event": "scraper_skipped",
                    "scraper": self.NAME,
                    "reason": "robots.txt",
                }))
                return []
            raw = await self.fetch()
            parsed = self.parse(raw)
            cars = self.normalize(parsed)
            self.logger.info(json.dumps({
                "event": "scraper_done",
                "scraper": self.NAME,
                "cars_found": len(cars),
            }))
            return cars
        except Exception as e:
            self.logger.error(json.dumps({
                "event": "scraper_error",
                "scraper": self.NAME,
                "error": str(e),
            }))
            return []
