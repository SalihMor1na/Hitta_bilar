"""Bytbil.com scraper using Playwright for JS-rendered search results."""
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

# Correct search URL — verified directly in Chrome by user
_BASE_SEARCH_URL = "https://www.bytbil.com/bil"
_SEARCH_MODELS = ["v60", "v90", "xc60"]
_BASE_PARAMS = {
    "VehicleType": "bil",
    "Makes": "Volvo",
    "PriceRange.To": "300000",
    "ModelYearRange.From": "2018",
    "ModelYearRange.To": "2027",
    "MilageRange.From": "0",
    "MilageRange.To": "16000",
    "SortParams.SortField": "publishedDate",
    "SortParams.IsAscending": "False",
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


def _parse_mileage(text: str) -> float:
    """Parse mileage in mil. Bytbil shows mileage in mil."""
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


class BytbilScraper(BaseScraper):
    NAME = "bytbil"
    BASE_URL = "https://www.bytbil.com"

    def __init__(self, http_client: HttpClient, cache: Cache) -> None:
        super().__init__(http_client, cache)

    async def fetch(self) -> list[dict]:
        """Fetch Bytbil listings using headless browser (page is JS-rendered)."""
        all_items: list[dict] = []

        for model in _SEARCH_MODELS:
            page = 1
            while page <= 10:
                params = {**_BASE_PARAMS, "FreeText": model, "Page": str(page)}
                url = f"{_BASE_SEARCH_URL}?{urllib.parse.urlencode(params)}"
                cache_key = f"bytbil:{model}:page{page}"
                cached = self.cache.get(cache_key)
                html = cached
                if not html:
                    self.logger.info(json.dumps({"event": "bytbil_browser_fetch", "model": model, "page": page}))
                    html = await fetch_rendered_html(
                        url,
                        wait_selector="article, [class*='car-list'], [class*='vehicle'], [class*='CarCard']",
                    )
                    if html:
                        self.cache.set(cache_key, html)

                if not html:
                    self.logger.warning(json.dumps({
                        "event": "fetch_empty", "scraper": self.NAME,
                        "model": model, "page": page,
                    }))
                    break

                soup = BeautifulSoup(html, "html.parser")
                cards = (
                    soup.select("article.car-list-card")
                    or soup.select("div.car-list-card")
                    or soup.select("[data-testid='car-card']")
                    or soup.select("article[class*='car']")
                    or soup.select("li.hit")
                    or soup.select("div[class*='vehicle-card']")
                    or soup.select("div[class*='result-item']")
                    or soup.select("article[class*='CarCard']")
                    or soup.select("article")
                )

                if not cards:
                    self.logger.info(json.dumps({
                        "event": "no_cards_found", "scraper": self.NAME,
                        "model": model, "page": page,
                        "html_snippet": soup.body.get_text(" ", strip=True)[:400] if soup.body else "",
                    }))
                    break

                for card in cards:
                    all_items.append({"html": str(card), "base_url": self.BASE_URL})

                next_btn = (
                    soup.select_one("a[rel='next']")
                    or soup.select_one(".pagination__next")
                    or soup.select_one("[aria-label='Nästa sida']")
                )
                if not next_btn or page >= 10:
                    break
                page += 1

        return all_items

    def parse(self, raw_data: list[dict]) -> list[dict]:
        parsed = []
        for item in raw_data:
            try:
                soup = BeautifulSoup(item["html"], "html.parser")
                base_url = item.get("base_url", self.BASE_URL)

                title_el = (
                    soup.select_one("h2.car-list-card__title")
                    or soup.select_one("h2[class*='title']")
                    or soup.select_one("h2")
                    or soup.select_one("[class*='title']")
                )
                title = title_el.get_text(strip=True) if title_el else ""

                model = _detect_model(title)
                if not model:
                    continue

                link_el = soup.select_one("a[href]")
                href = link_el["href"] if link_el else ""
                if href and not href.startswith("http"):
                    href = base_url + href
                url = href or ""

                price_el = (
                    soup.select_one("[class*='price']")
                    or soup.select_one("span.price")
                )
                price_text = price_el.get_text(strip=True) if price_el else "0"
                price_sek = _parse_price(price_text)

                year = 0
                year_el = soup.select_one("[class*='year']")
                if year_el:
                    m = re.search(r"(20\d{2}|19\d{2})", year_el.get_text())
                    if m:
                        year = int(m.group(1))
                if not year:
                    m = re.search(r"(20\d{2}|19\d{2})", title)
                    if m:
                        year = int(m.group(1))

                mileage_mil = 0.0
                mil_el = soup.select_one("[class*='mileage']") or soup.select_one("[class*='mil']")
                if mil_el:
                    mileage_mil = _parse_mileage(mil_el.get_text(strip=True))

                fuel_el = soup.select_one("[class*='fuel']") or soup.select_one("[class*='drivmedel']")
                fuel = _detect_fuel(fuel_el.get_text(strip=True) if fuel_el else title)

                full_text = soup.get_text(" ", strip=True)
                features, audio_system = _extract_features(full_text)

                parsed.append({
                    "source": self.NAME,
                    "url": url,
                    "model": model,
                    "year": year,
                    "price_sek": price_sek,
                    "mileage_mil": mileage_mil,
                    "fuel": fuel,
                    "features": features,
                    "audio_system": audio_system,
                    "title": title,
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
