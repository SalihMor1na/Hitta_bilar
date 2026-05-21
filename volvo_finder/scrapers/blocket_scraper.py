"""Blocket.se scraper using Playwright for JS-rendered search results."""
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

# Blocket public search — /mobility/search/car is the correct endpoint (verified in browser)
_SEARCH_MODELS = ["v60", "v90", "xc60"]
_BASE_SEARCH_URL = "https://www.blocket.se/mobility/search/car"
_MAX_PAGES = 5

# Query params matching what the browser sends (filter applied server-side)
_SEARCH_PARAMS = {
    "mileage_to": "16000",
    "price_to": "300000",
    "year_from": "2018",
}

_FEATURE_MAP = {
    "panoramatak": "panoramatak",
    "panoramic": "panoramatak",
    "backkamera": "backkamera",
    "back camera": "backkamera",
    "360-kamera": "360kamera",
    "360 kamera": "360kamera",
    "360°": "360kamera",
    "skinnklädsel": "skinnstolar",
    "skinn": "skinnstolar",
    "leather": "skinnstolar",
    "blis": "blis",
    "blind spot": "blis",
    "bowers & wilkins": "bowers_wilkins",
    "bowers&wilkins": "bowers_wilkins",
    "b&w": "bowers_wilkins",
    "harman kardon": "harman_kardon",
    "harman/kardon": "harman_kardon",
}

_MODEL_PATTERNS = {
    "V60": re.compile(r"\bV60\b", re.IGNORECASE),
    "V90": re.compile(r"\bV90\b", re.IGNORECASE),
    "XC60": re.compile(r"\bXC60\b", re.IGNORECASE),
}

_FUEL_MAP = {
    "diesel": "diesel",
    "bensin": "bensin",
    "el": "el",
    "laddhybrid": "laddhybrid",
    "recharge": "recharge",
    "hybrid": "hybrid",
    "miljöbränsle": "miljobransle",
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


def _extract_features_from_text(text: str) -> tuple[set[str], Optional[str]]:
    features: set[str] = set()
    audio_system: Optional[str] = None
    text_l = text.lower()
    for kw, feat in _FEATURE_MAP.items():
        if kw.lower() in text_l:
            features.add(feat)
    if "bowers" in text_l or "b&w" in text_l:
        audio_system = "Bowers & Wilkins"
    elif "harman" in text_l:
        audio_system = "Harman Kardon"
    return features, audio_system


class BlocketScraper(BaseScraper):
    NAME = "blocket"
    BASE_URL = "https://www.blocket.se"

    def __init__(self, http_client: HttpClient, cache: Cache) -> None:
        super().__init__(http_client, cache)

    async def fetch(self) -> list[dict]:
        """Fetch Blocket listings using headless browser (page is JS-rendered)."""
        all_items: list[dict] = []

        for model in _SEARCH_MODELS:
            for page in range(1, _MAX_PAGES + 1):
                params = {**_SEARCH_PARAMS, "q": model, "page": str(page)}
                url = f"{_BASE_SEARCH_URL}?{urllib.parse.urlencode(params)}"
                cache_key = f"blocket:{model}:page{page}"
                cached = self.cache.get(cache_key)
                html = cached
                if not html:
                    self.logger.info(json.dumps({"event": "blocket_browser_fetch", "model": model, "page": page}))
                    html = await fetch_rendered_html(
                        url,
                        wait_selector="[class*='item'], [class*='Item'], article, [class*='ad'], [class*='Ad']",
                    )
                    if html:
                        self.cache.set(cache_key, html)

                if not html:
                    self.logger.warning(json.dumps({
                        "event": "blocket_fetch_empty", "model": model, "page": page,
                    }))
                    break

                soup = BeautifulSoup(html, "html.parser")
                cards = (
                    soup.select("[class*='item-list__item']")
                    or soup.select("[class*='ItemCard']")
                    or soup.select("[class*='AdCard']")
                    or soup.select("[data-testid='ad-card']")
                    or soup.select("article[class*='ad']")
                    or soup.select("li[class*='item']")
                    or soup.select("article")
                )

                if not cards:
                    self.logger.info(json.dumps({
                        "event": "blocket_no_cards", "model": model, "page": page,
                        "html_snippet": soup.body.get_text(" ", strip=True)[:400] if soup.body else "",
                    }))
                    break

                self.logger.info(json.dumps({
                    "event": "blocket_cards_found", "model": model, "page": page, "count": len(cards),
                }))
                for card in cards:
                    all_items.append({"_html": str(card), "_base_url": self.BASE_URL})

                next_btn = (
                    soup.select_one("a[rel='next']")
                    or soup.select_one("[aria-label*='nästa']")
                    or soup.select_one("[aria-label*='Next']")
                )
                if not next_btn:
                    break

        return all_items

    def parse(self, raw_data: list[dict]) -> list[dict]:
        """Extract relevant fields from Blocket rendered HTML card items."""
        parsed = []
        for item in raw_data:
            try:
                soup = BeautifulSoup(item["_html"], "html.parser")
                base_url = item.get("_base_url", self.BASE_URL)
                full_text = soup.get_text(" ", strip=True)

                if "volvo" not in full_text.lower():
                    continue

                model = _detect_model(full_text)
                if not model:
                    continue

                link_el = soup.select_one("a[href]")
                href = link_el["href"] if link_el else ""
                if href and not href.startswith("http"):
                    href = base_url + href

                price_el = (
                    soup.select_one("[class*='price']")
                    or soup.select_one("[class*='Price']")
                )
                price_text = price_el.get_text(strip=True) if price_el else "0"
                price_sek = int(re.sub(r"[^\d]", "", price_text) or "0")

                year = 0
                m_yr = re.search(r"(20\d{2}|19\d{2})", full_text)
                if m_yr:
                    year = int(m_yr.group(1))

                mileage_mil = 0.0
                m_mil = re.search(r"(\d[\d\s]*)\s*mil", full_text, re.IGNORECASE)
                if m_mil:
                    raw_mil = re.sub(r"[^\d,.]", "", m_mil.group(0)).replace(",", ".")
                    try:
                        mileage_mil = float(raw_mil) if raw_mil else 0.0
                    except ValueError:
                        pass

                fuel = _detect_fuel(full_text)
                features, audio_system = _extract_features_from_text(full_text)

                parsed.append({
                    "source": self.NAME,
                    "url": href,
                    "model": model,
                    "year": year,
                    "price_sek": price_sek,
                    "mileage_mil": mileage_mil,
                    "fuel": fuel,
                    "features": features,
                    "audio_system": audio_system,
                    "title": full_text[:80],
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
                source=d["source"],
                url=d["url"],
                model=d["model"],
                year=d.get("year", 0),
                price_sek=d.get("price_sek", 0),
                mileage_mil=d.get("mileage_mil", 0.0),
                fuel=d.get("fuel", "okänd"),
                features=d.get("features", set()),
                audio_system=d.get("audio_system"),
                title=d.get("title"),
            ))
        return cars
