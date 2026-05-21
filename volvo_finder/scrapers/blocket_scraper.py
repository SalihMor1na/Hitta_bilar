"""Blocket.se scraper using Blocket's public search API."""
import json
import re
from typing import Optional

from models.car import Car
from scrapers.base_scraper import BaseScraper
from utils.cache import Cache
from utils.http_client import HttpClient

# Blocket car category API
_API_URL = "https://api.blocket.se/search_bff/v1/content"
_DEFAULT_PARAMS = {
    "cg": "1020",   # Cars category
    "st": "s",       # Sell
    "lim": "60",
}
_SEARCH_QUERIES = ["volvo v60", "volvo v90", "volvo xc60"]

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

_AUDIO_KEYWORDS = {
    "bowers_wilkins": ["bowers", "b&w"],
    "harman_kardon": ["harman kardon", "harman/kardon"],
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


def _extract_features(parameters: list[dict]) -> tuple[set[str], Optional[str]]:
    """Extract features set and audio_system from Blocket parameters list."""
    features: set[str] = set()
    audio_system: Optional[str] = None
    for param in parameters:
        label = str(param.get("label", "")).lower()
        value = str(param.get("value", "")).lower()
        combined = f"{label} {value}"
        for kw, feat in _FEATURE_MAP.items():
            if kw.lower() in combined:
                features.add(feat)
        # Detect audio
        if "bowers" in combined or "b&w" in combined:
            audio_system = "Bowers & Wilkins"
        elif "harman" in combined:
            audio_system = "Harman Kardon"
    return features, audio_system


class BlocketScraper(BaseScraper):
    NAME = "blocket"
    BASE_URL = "https://www.blocket.se"

    def __init__(self, http_client: HttpClient, cache: Cache) -> None:
        super().__init__(http_client, cache)

    async def fetch(self) -> list[dict]:
        """Fetch all Volvo listings from Blocket, paginating through results."""
        all_items: list[dict] = []
        for query in _SEARCH_QUERIES:
            page = 0
            while True:
                params = {**_DEFAULT_PARAMS, "q": query, "page": str(page)}
                cache_key = f"{_API_URL}?q={query}&page={page}"
                cached = self.cache.get(cache_key)
                if cached is not None:
                    try:
                        data = json.loads(cached)
                    except json.JSONDecodeError:
                        data = None
                else:
                    data = await self.http_client.get_json(_API_URL, params=params)
                    if data is not None:
                        self.cache.set(cache_key, json.dumps(data))

                if not data:
                    break

                items = data.get("data", [])
                if not items:
                    break
                all_items.extend(items)

                # Check if there are more pages
                total_count = data.get("total_count", 0)
                fetched_so_far = (page + 1) * int(_DEFAULT_PARAMS["lim"])
                if fetched_so_far >= total_count or len(items) < int(_DEFAULT_PARAMS["lim"]):
                    break
                page += 1

        return all_items

    def parse(self, raw_data: list[dict]) -> list[dict]:
        """Extract relevant fields from Blocket API items."""
        parsed = []
        for item in raw_data:
            try:
                subject = item.get("subject", "")
                # Filter to only Volvo listings
                if "volvo" not in subject.lower():
                    continue

                model = _detect_model(subject)
                if model is None:
                    # Try parameters
                    for p in item.get("parameters", []):
                        if p.get("label", "").lower() in ("märke", "make"):
                            pass
                        if p.get("label", "").lower() in ("modell", "model"):
                            model = _detect_model(str(p.get("value", "")))
                if model is None:
                    continue

                price_data = item.get("price", {}) or {}
                price = int(price_data.get("amount", 0) or 0)

                parameters = item.get("parameters", []) or []

                year = 0
                mileage_mil = 0.0
                fuel = "okänd"
                color = None
                gearbox = None

                for p in parameters:
                    label = (p.get("label") or "").lower()
                    value = str(p.get("value") or "")
                    if label in ("modelår", "year", "år"):
                        try:
                            year = int(value)
                        except ValueError:
                            pass
                    elif label in ("miltal", "mileage", "mil"):
                        try:
                            # Remove non-numeric chars
                            num = re.sub(r"[^\d,.]", "", value).replace(",", ".")
                            mileage_mil = float(num) if num else 0.0
                        except ValueError:
                            pass
                    elif label in ("drivmedel", "fuel", "bränsle"):
                        fuel = _detect_fuel(value)
                    elif label in ("färg", "color"):
                        color = value
                    elif label in ("växellåda", "gearbox", "transmission"):
                        gearbox = value

                features, audio_system = _extract_features(parameters)
                # Also scan description
                body = item.get("body", "") or ""
                for kw, feat in _FEATURE_MAP.items():
                    if kw.lower() in body.lower():
                        features.add(feat)
                if "bowers" in body.lower():
                    audio_system = audio_system or "Bowers & Wilkins"
                elif "harman" in body.lower():
                    audio_system = audio_system or "Harman Kardon"

                ad_id = item.get("ad_id") or item.get("list_id") or ""
                url = item.get("share_url") or f"https://www.blocket.se/annons/{ad_id}"

                parsed.append({
                    "source": self.NAME,
                    "url": url,
                    "model": model,
                    "year": year,
                    "price_sek": price,
                    "mileage_mil": mileage_mil,
                    "fuel": fuel,
                    "features": features,
                    "audio_system": audio_system,
                    "vin": item.get("vin"),
                    "title": subject,
                    "color": color,
                    "gearbox": gearbox,
                })
            except Exception as e:
                self.logger.warning(json.dumps({"event": "parse_error", "scraper": self.NAME, "error": str(e)}))
        return parsed

    def normalize(self, parsed: list[dict]) -> list[Car]:
        cars = []
        for d in parsed:
            if not d.get("url") or not d.get("model") or not d.get("year"):
                continue
            cars.append(Car(
                source=d["source"],
                url=d["url"],
                model=d["model"],
                year=d["year"],
                price_sek=d["price_sek"],
                mileage_mil=d["mileage_mil"],
                fuel=d["fuel"],
                features=d.get("features", set()),
                audio_system=d.get("audio_system"),
                vin=d.get("vin"),
                title=d.get("title"),
                color=d.get("color"),
                gearbox=d.get("gearbox"),
            ))
        return cars
