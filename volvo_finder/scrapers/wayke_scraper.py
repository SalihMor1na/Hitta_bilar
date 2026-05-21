"""Wayke.se scraper using their public vehicle search API."""
import json
import re
from typing import Optional

from models.car import Car
from scrapers.base_scraper import BaseScraper
from utils.cache import Cache
from utils.http_client import HttpClient

_API_URL = "https://api.wayke.se/v1/vehicles"
_SEARCH_PARAMS = {
    "make": "Volvo",
    "priceMax": "300000",
    "yearMin": "2018",
    "pageSize": "50",
}
_MODELS = ["V60", "V90", "XC60"]

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


class WaykeScraper(BaseScraper):
    NAME = "wayke"
    BASE_URL = "https://www.wayke.se"

    def __init__(self, http_client: HttpClient, cache: Cache) -> None:
        super().__init__(http_client, cache)

    async def fetch(self) -> list[dict]:
        """Fetch Volvo listings from Wayke API, paginating through models."""
        all_items: list[dict] = []
        for model in _MODELS:
            page = 1
            while True:
                params = {**_SEARCH_PARAMS, "model": model, "page": str(page)}
                cache_key = f"{_API_URL}?model={model}&page={page}"
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

                # Wayke API response format: {"vehicles": [...], "totalCount": N}
                vehicles = data.get("vehicles") or data.get("items") or data.get("data") or []
                if not vehicles:
                    break
                all_items.extend(vehicles)

                total = data.get("totalCount") or data.get("total_count") or 0
                page_size = int(_SEARCH_PARAMS["pageSize"])
                if page * page_size >= total or len(vehicles) < page_size:
                    break
                page += 1

        return all_items

    def parse(self, raw_data: list[dict]) -> list[dict]:
        parsed = []
        for item in raw_data:
            try:
                # Wayke vehicle fields
                make = str(item.get("make") or item.get("manufacturer") or "")
                if "volvo" not in make.lower():
                    continue

                model_raw = str(item.get("model") or item.get("modelName") or "")
                # Map to allowed models
                model = None
                for m in _MODELS:
                    if m.lower() in model_raw.lower():
                        model = m
                        break
                if not model:
                    continue

                price_sek = int(item.get("price") or item.get("sellingPrice") or 0)
                year = int(item.get("modelYear") or item.get("year") or 0)

                # Mileage may be in km or mil
                mileage_raw = item.get("mileage") or item.get("odometer") or 0
                mileage_km = float(mileage_raw)
                # Wayke typically uses km; convert to mil
                mileage_mil = mileage_km / 10.0

                fuel = _detect_fuel(str(item.get("fuelType") or item.get("fuel") or ""))
                color = item.get("color") or item.get("exteriorColor")
                gearbox = item.get("gearbox") or item.get("transmission")
                vin = item.get("vin") or item.get("registrationNumber")

                # Feature extraction from description + equipment list
                equipment = item.get("equipment") or item.get("features") or []
                equip_text = " ".join(str(e) for e in equipment) if isinstance(equipment, list) else str(equipment)
                description = str(item.get("description") or "")
                full_text = f"{equip_text} {description}"
                features, audio_system = _extract_features(full_text)

                url = (
                    item.get("url")
                    or item.get("shareUrl")
                    or f"https://www.wayke.se/bil/{item.get('id', '')}"
                )

                title = f"Volvo {model} {year}"

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
                    "vin": vin,
                    "title": title,
                    "color": color,
                    "gearbox": gearbox,
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
                vin=d.get("vin"),
                title=d.get("title"),
                color=d.get("color"),
                gearbox=d.get("gearbox"),
            ))
        return cars
