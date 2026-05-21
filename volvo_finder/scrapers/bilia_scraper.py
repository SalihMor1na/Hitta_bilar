"""Bilia.se scraper using BeautifulSoup4 HTML parsing."""
import json
import re
from typing import Optional

from bs4 import BeautifulSoup

from models.car import Car
from scrapers.base_scraper import BaseScraper
from utils.cache import Cache
from utils.http_client import HttpClient

_SEARCH_URL = "https://www.bilia.se/begagnade-bilar/"
_SEARCH_PARAMS = {
    "make": "Volvo",
    "models": "V60,V90,XC60",
    "maxPrice": "300000",
    "yearFrom": "2018",
}

_MODEL_PATTERNS = {
    "V60": re.compile(r"\bV60\b", re.IGNORECASE),
    "V90": re.compile(r"\bV90\b", re.IGNORECASE),
    "XC60": re.compile(r"\bXC60\b", re.IGNORECASE),
}

_FEATURE_KEYWORDS = {
    "panoramatak": "panoramatak",
    "panoramic": "panoramatak",
    "panoramic roof": "panoramatak",
    "backkamera": "backkamera",
    "360": "360kamera",
    "skinnklädsel": "skinnstolar",
    "skinn": "skinnstolar",
    "leather": "skinnstolar",
    "blis": "blis",
    "blind spot": "blis",
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


class BiliaScraper(BaseScraper):
    NAME = "bilia"
    BASE_URL = "https://www.bilia.se"

    def __init__(self, http_client: HttpClient, cache: Cache) -> None:
        super().__init__(http_client, cache)

    async def fetch(self) -> list[dict]:
        """Fetch Bilia listings with pagination."""
        all_items: list[dict] = []
        page = 1
        while True:
            params = {**_SEARCH_PARAMS, "page": str(page)}
            cache_key = f"{_SEARCH_URL}?page={page}"
            cached = self.cache.get(cache_key)
            if cached is not None:
                html = cached
            else:
                html = await self.http_client.get_text(_SEARCH_URL, params=params)
                if html:
                    self.cache.set(cache_key, html)

            if not html:
                self.logger.warning(json.dumps({"event": "fetch_empty", "scraper": self.NAME, "page": page}))
                break

            all_items.append({"html": html, "base_url": self.BASE_URL})

            soup = BeautifulSoup(html, "html.parser")
            next_btn = soup.select_one("a[rel='next']") or soup.select_one(".pagination-next")
            if not next_btn or page >= 15:
                break
            page += 1

        return all_items

    def parse(self, raw_data: list[dict]) -> list[dict]:
        parsed = []
        for item in raw_data:
            try:
                soup = BeautifulSoup(item["html"], "html.parser")
                base_url = item.get("base_url", self.BASE_URL)

                cards = (
                    soup.select("article.vehicle-item")
                    or soup.select("div.car-item")
                    or soup.select("[class*='vehicle-card']")
                    or soup.select("[class*='car-listing']")
                    or soup.select("li.search-result-item")
                )

                if not cards:
                    self.logger.warning(json.dumps({"event": "no_cards", "scraper": self.NAME}))
                    continue

                for card in cards:
                    try:
                        full_text = card.get_text(" ", strip=True)
                        title_el = card.select_one("h2") or card.select_one("h3") or card.select_one("[class*='title']")
                        title = title_el.get_text(strip=True) if title_el else full_text[:100]

                        if "volvo" not in title.lower() and "volvo" not in full_text.lower():
                            continue

                        model = _detect_model(title) or _detect_model(full_text)
                        if not model:
                            continue

                        link_el = card.select_one("a[href]")
                        href = link_el["href"] if link_el else ""
                        if href and not href.startswith("http"):
                            href = base_url + href
                        url = href or ""

                        price_el = card.select_one("[class*='price']") or card.select_one("[class*='pris']")
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
                        self.logger.warning(json.dumps({"event": "card_parse_error", "scraper": self.NAME, "error": str(e)}))
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
