"""Bilhall.se scraper — site is currently unavailable (503)."""
import json

from models.car import Car
from scrapers.base_scraper import BaseScraper
from utils.cache import Cache
from utils.http_client import HttpClient


class BilhallScraper(BaseScraper):
    NAME = "bilhall"
    BASE_URL = "https://www.bilhall.se"

    def __init__(self, http_client: HttpClient, cache: Cache) -> None:
        super().__init__(http_client, cache)

    async def fetch(self) -> list[dict]:
        """Bilhall returns 503 on all requests — site is unavailable."""
        self.logger.info(json.dumps({
            "event": "scraper_unavailable",
            "scraper": self.NAME,
            "reason": "Site returns 503 on all requests",
        }))
        return []

    def parse(self, raw_data: list[dict]) -> list[dict]:
        return []

    def normalize(self, parsed: list[dict]) -> list[Car]:
        return []
