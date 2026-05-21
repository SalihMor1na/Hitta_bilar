"""Wayke.se scraper — uses Playwright for JS-rendered search results."""
import json
import re
from typing import Optional

from bs4 import BeautifulSoup

from models.car import Car
from scrapers.base_scraper import BaseScraper
from utils.browser_client import fetch_rendered_html
from utils.cache import Cache
from utils.http_client import HttpClient

# Correct URL verified by user in Chrome
_BASE_SEARCH_URL = "https://www.wayke.se/sok"
_SEARCH_MODELS = ["V60", "V90", "XC60"]
_BASE_PARAMS = {
    "mileage.max": "16000",
    "modelYear.min": "2018",
    "price.max": "300000",
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
    "bowers&wilkins": "bowers_wilkins",
    "harman kardon": "harman_kardon",
    "harman/kardon": "harman_kardon",
}

_FUEL_MAP = {
    "diesel": "diesel",
    "bensin": "bensin",
    "el": "el",
    "laddhybrid": "laddhybrid",
    "recharge": "recharge",
    "hybrid": "hybrid",
    "phev": "recharge",
    "petrol": "bensin",
    "electric": "el",
}


def _detect_fuel(text: str) -> str:
    text_l = text.lower()
    for kw, fuel in _FUEL_MAP.items():
        if kw in text_l:
            return fuel
    return "okänd"


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


def _build_url(model: str, page: int) -> str:
    params = {**_BASE_PARAMS, "modelSeries": model}
    if page > 1:
        params["page"] = str(page)
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{_BASE_SEARCH_URL}?{qs}"


class WaykeScraper(BaseScraper):
    NAME = "wayke"
    BASE_URL = "https://www.wayke.se"

    def __init__(self, http_client: HttpClient, cache: Cache) -> None:
        super().__init__(http_client, cache)

    async def fetch(self) -> list[dict]:
        """Fetch Wayke listings using headless browser (page is JS-rendered)."""
        all_items: list[dict] = []

        for model in _SEARCH_MODELS:
            page = 1
            while page <= 5:
                url = _build_url(model, page)
                cache_key = f"wayke:{model}:page{page}"
                cached = self.cache.get(cache_key)
                if cached is not None:
                    html = cached
                else:
                    self.logger.info(json.dumps({"event": "wayke_browser_fetch", "model": model, "page": page}))
                    # Wait for car cards to appear
                    html = await fetch_rendered_html(
                        url,
                        wait_selector="[class*='vehicle'], [class*='Vehicle'], article, [class*='card']",
                    )
                    if html:
                        self.cache.set(cache_key, html)

                if not html:
                    self.logger.warning(json.dumps({"event": "wayke_fetch_empty", "model": model, "page": page}))
                    break

                soup = BeautifulSoup(html, "html.parser")
                cards = (
                    soup.select("[class*='vehicle-card']")
                    or soup.select("[class*='VehicleCard']")
                    or soup.select("[class*='SearchItem']")
                    or soup.select("[class*='search-item']")
                    or soup.select("article[class*='vehicle']")
                    or soup.select("[class*='car-card']")
                    or soup.select("li.hit")
                )

                if not cards:
                    self.logger.info(json.dumps({
                        "event": "wayke_no_cards", "model": model, "page": page,
                        "html_snippet": soup.body.get_text(" ", strip=True)[:400] if soup.body else "",
                    }))
                    break

                self.logger.info(json.dumps({"event": "wayke_cards_found", "model": model, "page": page, "count": len(cards)}))
                for card in cards:
                    all_items.append({"_html": str(card), "_base_url": self.BASE_URL})

                next_btn = (
                    soup.select_one("a[rel='next']")
                    or soup.select_one("[aria-label*='nästa']")
                    or soup.select_one("[aria-label*='Next']")
                )
                if not next_btn:
                    break
                page += 1

        return all_items

    def parse(self, raw_data: list[dict]) -> list[dict]:
        parsed = []
        for item in raw_data:
            try:
                soup = BeautifulSoup(item["_html"], "html.parser")
                base_url = item.get("_base_url", self.BASE_URL)
                full_text = soup.get_text(" ", strip=True)

                model = None
                for m in ["V60", "V90", "XC60"]:
                    if re.search(rf"\b{m}\b", full_text, re.IGNORECASE):
                        model = m
                        break
                if not model:
                    continue

                link_el = soup.select_one("a[href]")
                href = link_el["href"] if link_el else ""
                if href and not href.startswith("http"):
                    href = base_url + href

                price_el = soup.select_one("[class*='price']") or soup.select_one("[class*='Price']")
                price_sek = int(re.sub(r"[^\d]", "", price_el.get_text()) if price_el else "0") or 0

                year = 0
                m_yr = re.search(r"(20\d{2}|19\d{2})", full_text)
                if m_yr:
                    year = int(m_yr.group(1))

                mileage_mil = 0.0
                m_mil = re.search(r"(\d[\d\s]*)\s*mil", full_text, re.IGNORECASE)
                if m_mil:
                    raw_mil = re.sub(r"[^\d,.]", "", m_mil.group(0)).replace(",", ".")
                    mileage_mil = float(raw_mil) if raw_mil else 0.0

                fuel = _detect_fuel(full_text)
                features, audio_system = _extract_features(full_text)

                parsed.append({
                    "source": self.NAME, "url": href, "model": model,
                    "year": year, "price_sek": price_sek, "mileage_mil": mileage_mil,
                    "fuel": fuel, "features": features, "audio_system": audio_system,
                    "title": f"Volvo {model} {year}",
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
