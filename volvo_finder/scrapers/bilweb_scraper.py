"""Bilweb.se scraper using Playwright for JS-rendered search results."""
import json
import re
import urllib.parse
from typing import Optional

from bs4 import BeautifulSoup

from models.car import Car
from scrapers.base_scraper import BaseScraper
from utils.browser_client import fetch_rendered_html
from utils.cache import Cache
from utils.http_client import HttpClient

# Correct URL verified by user in Chrome
_SEARCH_URL = "https://bilweb.se/sok"
_BASE_PARAMS = {
    "limit": "30",
    "order_by": "timestamp",
    "order": "desc",
    "type": "1",
    "brand": "150",        # Volvo brand ID
    "price_min": "0",
    "price_max": "300000",
    "year_min": "2018",
    "year_max": "2027",
    "property_mileage_max": "16000",
}

_MODEL_PATTERNS = {
    "V60": re.compile(r"\bV60\b", re.IGNORECASE),
    "V90": re.compile(r"\bV90\b", re.IGNORECASE),
    "XC60": re.compile(r"\bXC60\b", re.IGNORECASE),
}

_FEATURE_KEYWORDS = {
    "panoramatak": "panoramatak",
    "panoramic": "panoramatak",
    "backkamera": "backkamera",
    "360": "360kamera",
    "skinnklädsel": "skinnstolar",
    "skinn": "skinnstolar",
    "blis": "blis",
    "bowers & wilkins": "bowers_wilkins",
    "harman kardon": "harman_kardon",
    "harman/kardon": "harman_kardon",
}

_FUEL_MAP = {
    "diesel": "diesel",
    "bensin": "bensin",
    "el": "el",
    "laddhybrid": "laddhybrid",
    "recharge": "recharge",
    "phev": "recharge",
    "hybrid": "hybrid",
}


def _detect_model(text: str) -> Optional[str]:
    for model, pat in _MODEL_PATTERNS.items():
        if pat.search(text):
            return model
    return None


def _detect_fuel(text: str) -> str:
    text_l = text.lower()
    for kw, fuel in _FUEL_MAP.items():
        if kw in text_l:
            return fuel
    return "okänd"


def _parse_price(text: str) -> int:
    nums = re.sub(r"[^\d]", "", text)
    return int(nums) if nums else 0


def _parse_mileage_mil(text: str) -> float:
    text = text.lower().replace("mil", "").strip()
    num = re.sub(r"[^\d,.]", "", text).replace(",", ".")
    try:
        return float(num) if num else 0.0
    except ValueError:
        return 0.0


def _extract_features(text: str) -> tuple[set[str], Optional[str]]:
    features: set[str] = set()
    audio_system: Optional[str] = None
    text_l = text.lower()
    for kw, feat in _FEATURE_KEYWORDS.items():
        if kw.lower() in text_l:
            features.add(feat)
    if "bowers" in text_l:
        audio_system = "Bowers & Wilkins"
    elif "harman" in text_l:
        audio_system = "Harman Kardon"
    return features, audio_system


class BilwebScraper(BaseScraper):
    NAME = "bilweb"
    BASE_URL = "https://bilweb.se"

    def __init__(self, http_client: HttpClient, cache: Cache) -> None:
        super().__init__(http_client, cache)

    async def fetch(self) -> list[dict]:
        """Fetch Bilweb listings using headless browser (page is JS-rendered)."""
        all_items: list[dict] = []
        limit = int(_BASE_PARAMS["limit"])
        offset = 0

        while offset <= 300:
            params = {**_BASE_PARAMS, "offset": str(offset)}
            url = f"{_SEARCH_URL}?{urllib.parse.urlencode(params)}"
            cache_key = f"playwright:bilweb:offset{offset}"
            cached = self.cache.get(cache_key)
            html = cached
            if not html:
                self.logger.info(json.dumps({"event": "bilweb_browser_fetch", "offset": offset}))
                html = await fetch_rendered_html(
                    url,
                    wait_selector="[class*='car'], [class*='Car'], [class*='vehicle'], article, [class*='listing']",
                    delay_ms=8000,
                )
                if html:
                    self.cache.set(cache_key, html)

            if not html:
                self.logger.warning(json.dumps({"event": "fetch_empty", "scraper": self.NAME, "offset": offset}))
                break

            soup = BeautifulSoup(html, "html.parser")
            cards = (
                soup.select("div.car-item")
                or soup.select("article.car")
                or soup.select("[class*='car-card']")
                or soup.select("li.search-item")
                or soup.select("div[class*='listing']")
                or soup.select("[class*='CarCard']")
                or soup.select("[class*='vehicle-item']")
                or soup.select("article")
            )

            if not cards:
                self.logger.info(json.dumps({
                    "event": "bilweb_no_cards", "offset": offset,
                    "html_snippet": soup.body.get_text(" ", strip=True)[:400] if soup.body else "",
                }))
                break

            self.logger.info(json.dumps({
                "event": "bilweb_cards_found", "offset": offset, "count": len(cards),
            }))
            for card in cards:
                all_items.append({"_html": str(card), "_base_url": self.BASE_URL})

            if len(cards) < limit:
                break
            offset += limit

        return all_items

    def parse(self, raw_data: list[dict]) -> list[dict]:
        parsed = []
        for item in raw_data:
            try:
                soup = BeautifulSoup(item["_html"], "html.parser")
                base_url = item.get("_base_url", self.BASE_URL)
                full_text = soup.get_text(" ", strip=True)

                title_el = soup.select_one("h2") or soup.select_one("h3") or soup.select_one("[class*='title']")
                title = title_el.get_text(strip=True) if title_el else full_text[:80]

                model = _detect_model(title) or _detect_model(full_text)
                if not model:
                    continue

                link_el = soup.select_one("a[href]")
                href = link_el["href"] if link_el else ""
                if href and not href.startswith("http"):
                    href = base_url + href

                price_el = soup.select_one("[class*='price']") or soup.select_one("[class*='pris']")
                price_sek = _parse_price(price_el.get_text(strip=True) if price_el else "0")

                year = 0
                m = re.search(r"(20\d{2}|19\d{2})", title)
                if m:
                    year = int(m.group(1))

                mileage_mil = 0.0
                m = re.search(r"(\d[\d\s]*)\s*mil", full_text, re.IGNORECASE)
                if m:
                    mileage_mil = _parse_mileage_mil(m.group(0))

                fuel = _detect_fuel(full_text)
                features, audio_system = _extract_features(full_text)

                parsed.append({
                    "source": self.NAME, "url": href, "model": model,
                    "year": year, "price_sek": price_sek, "mileage_mil": mileage_mil,
                    "fuel": fuel, "features": features, "audio_system": audio_system, "title": title,
                })
            except Exception as e:
                self.logger.warning(json.dumps({"event": "parse_error", "scraper": self.NAME, "error": str(e)}))
        return parsed

    def normalize(self, parsed: list[dict]) -> list[Car]:
        cars = []
        for d in parsed:
            if not d.get("url") or not d.get("model"):
                continue
            cars.append(Car(
                source=d["source"], url=d["url"], model=d["model"],
                year=d.get("year", 0), price_sek=d.get("price_sek", 0),
                mileage_mil=d.get("mileage_mil", 0.0), fuel=d.get("fuel", "okänd"),
                features=d.get("features", set()), audio_system=d.get("audio_system"),
                title=d.get("title"),
            ))
        return cars
