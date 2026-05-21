"""Blocket.se scraper using public HTML pages with __NEXT_DATA__ extraction."""
import json
import re
from typing import Optional

from models.car import Car
from scrapers.base_scraper import BaseScraper
from utils.cache import Cache
from utils.http_client import HttpClient
from utils.nextjs import extract_next_data, find_listings_in_next_data

# Blocket public search — /mobility/search/car is the correct endpoint (verified in browser)
_SEARCH_MODELS = ["v60", "v90", "xc60"]
_BASE_SEARCH_URL = "https://www.blocket.se/mobility/search/car"
_WARMUP_URL = "https://www.blocket.se/"
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
        if "bowers" in combined or "b&w" in combined:
            audio_system = "Bowers & Wilkins"
        elif "harman" in combined:
            audio_system = "Harman Kardon"
    return features, audio_system


def _find_listings(data: dict) -> list[dict]:
    """Try multiple known paths in __NEXT_DATA__ to locate car listings."""
    # Path 1: dehydratedState queries
    try:
        listings = (
            data["props"]["pageProps"]["dehydratedState"]["queries"][0]["state"]["data"]["data"]
        )
        if isinstance(listings, list) and listings:
            return listings
    except (KeyError, IndexError, TypeError):
        pass

    # Path 2: direct pageProps listings
    try:
        listings = data["props"]["pageProps"]["listings"]
        if isinstance(listings, list) and listings:
            return listings
    except (KeyError, TypeError):
        pass

    # Path 3: pageProps data.data
    try:
        listings = data["props"]["pageProps"]["data"]["data"]
        if isinstance(listings, list) and listings:
            return listings
    except (KeyError, TypeError):
        pass

    # Path 4: recursive search for car-like dicts
    return find_listings_in_next_data(data)


class BlocketScraper(BaseScraper):
    NAME = "blocket"
    BASE_URL = "https://www.blocket.se"

    def __init__(self, http_client: HttpClient, cache: Cache) -> None:
        super().__init__(http_client, cache)

    async def fetch(self) -> list[dict]:
        """Fetch all Volvo listings from Blocket using public HTML + __NEXT_DATA__."""
        await self.http_client.warm_up(_WARMUP_URL)

        all_items: list[dict] = []
        for model in _SEARCH_MODELS:
            for page in range(1, _MAX_PAGES + 1):
                params = {**_SEARCH_PARAMS, "q": model, "page": str(page)}
                cache_key = f"blocket:{model}:page{page}"
                cached = self.cache.get(cache_key)
                if cached is not None:
                    html = cached
                else:
                    html = await self.http_client.get_text(
                        _BASE_SEARCH_URL, params=params
                    )
                    if html:
                        self.cache.set(cache_key, html)

                if not html:
                    self.logger.warning(json.dumps({
                        "event": "blocket_fetch_empty",
                        "model": model,
                        "page": page,
                    }))
                    break

                try:
                    next_data = extract_next_data(html)
                except Exception as e:
                    self.logger.warning(json.dumps({
                        "event": "blocket_next_data_error",
                        "model": model,
                        "page": page,
                        "error": str(e),
                    }))
                    next_data = None

                if not next_data:
                    self.logger.info(json.dumps({
                        "event": "blocket_no_next_data",
                        "model": model,
                        "page": page,
                        "hint": "__NEXT_DATA__ not found — page may be JS-rendered",
                    }))
                    break

                listings = _find_listings(next_data)
                if not listings:
                    self.logger.info(json.dumps({
                        "event": "blocket_no_listings",
                        "model": model,
                        "page": page,
                    }))
                    break

                all_items.extend(listings)

                if len(listings) < 20:
                    break

        return all_items

    def parse(self, raw_data: list[dict]) -> list[dict]:
        """Extract relevant fields from Blocket __NEXT_DATA__ listing items."""
        parsed = []
        for item in raw_data:
            try:
                subject = item.get("subject", "") or item.get("title", "") or ""
                if "volvo" not in subject.lower():
                    continue

                model = _detect_model(subject)
                if model is None:
                    for p in item.get("parameters", []):
                        if p.get("label", "").lower() in ("modell", "model"):
                            model = _detect_model(str(p.get("value", "")))
                if model is None:
                    continue

                price_data = item.get("price", {}) or {}
                if isinstance(price_data, dict):
                    price = int(price_data.get("value", 0) or price_data.get("amount", 0) or 0)
                else:
                    try:
                        price = int(price_data)
                    except (ValueError, TypeError):
                        price = 0

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
                body = item.get("body", "") or item.get("description", "") or ""
                for kw, feat in _FEATURE_MAP.items():
                    if kw.lower() in body.lower():
                        features.add(feat)
                if "bowers" in body.lower():
                    audio_system = audio_system or "Bowers & Wilkins"
                elif "harman" in body.lower():
                    audio_system = audio_system or "Harman Kardon"

                ad_id = item.get("ad_id") or item.get("list_id") or item.get("id") or ""
                url = (
                    item.get("share_url")
                    or item.get("url")
                    or f"https://www.blocket.se/annons/{ad_id}"
                )

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
