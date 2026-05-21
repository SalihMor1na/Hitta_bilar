"""Volvo Selekt (Official Used Cars) scraper."""
import json
import re
from typing import Optional

from models.car import Car
from scrapers.base_scraper import BaseScraper
from utils.cache import Cache
from utils.http_client import HttpClient

# Volvo Selekt API endpoints to try
_API_URLS = [
    "https://selekt.volvocars.se/api/cars",
    "https://www.volvocars.com/api/used-cars/v1/vehicles",
]
_FALLBACK_HTML_URL = "https://selekt.volvocars.se/se/cars"

_API_PARAMS = {
    "country": "SE",
    "make": "Volvo",
    "pageSize": "50",
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
    "rear camera": "backkamera",
    "360": "360kamera",
    "surround camera": "360kamera",
    "skinnklädsel": "skinnstolar",
    "leather": "skinnstolar",
    "skinn": "skinnstolar",
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
    "petrol": "bensin",
    "electric": "el",
    "plug-in": "recharge",
}

_ALLOWED_MODELS = {"V60", "V90", "XC60"}


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


def _extract_features(equipment_list: list, description: str = "") -> tuple[set[str], Optional[str]]:
    features: set[str] = set()
    audio_system: Optional[str] = None

    combined = description.lower()
    if isinstance(equipment_list, list):
        for e in equipment_list:
            combined += " " + str(e).lower()

    for kw, feat in _FEATURE_KEYWORDS.items():
        if kw.lower() in combined:
            features.add(feat)
    if "bowers" in combined:
        audio_system = "Bowers & Wilkins"
    elif "harman" in combined:
        audio_system = "Harman Kardon"
    return features, audio_system


class VolvoSelektScraper(BaseScraper):
    NAME = "volvo_selekt"
    BASE_URL = "https://selekt.volvocars.se"

    def __init__(self, http_client: HttpClient, cache: Cache) -> None:
        super().__init__(http_client, cache)

    async def fetch(self) -> list[dict]:
        """Try Volvo Selekt API, fall back to HTML scraping."""
        all_items: list[dict] = []

        # Try API endpoints first
        for api_url in _API_URLS:
            page = 1
            while True:
                params = {**_API_PARAMS, "page": str(page)}
                cache_key = f"{api_url}?page={page}"
                cached = self.cache.get(cache_key)
                if cached is not None:
                    try:
                        data = json.loads(cached)
                    except json.JSONDecodeError:
                        data = None
                else:
                    data = await self.http_client.get_json(api_url, params=params)
                    if data is not None:
                        self.cache.set(cache_key, json.dumps(data))

                if not data:
                    break

                # Handle various response structures
                vehicles = (
                    data.get("vehicles")
                    or data.get("cars")
                    or data.get("items")
                    or data.get("data")
                    or (data if isinstance(data, list) else [])
                )

                if not vehicles:
                    break

                all_items.extend(vehicles if isinstance(vehicles, list) else [])

                total = data.get("totalCount") or data.get("total") or 0
                page_size = int(_API_PARAMS.get("pageSize", 50))
                if page * page_size >= total or len(vehicles) < page_size:
                    break
                page += 1

            if all_items:
                break  # Successfully fetched from first working API

        # If API fails, try HTML scraping
        if not all_items:
            cached = self.cache.get(_FALLBACK_HTML_URL)
            if cached is not None:
                html = cached
            else:
                html = await self.http_client.get_text(_FALLBACK_HTML_URL)
                if html:
                    self.cache.set(_FALLBACK_HTML_URL, html)
            if html:
                all_items.append({"_html": html, "_source": "html"})

        return all_items

    def parse(self, raw_data: list[dict]) -> list[dict]:
        parsed = []
        for item in raw_data:
            try:
                # Handle HTML fallback
                if item.get("_html"):
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(item["_html"], "html.parser")
                    cards = (
                        soup.select("[class*='car-card']")
                        or soup.select("article[class*='vehicle']")
                        or soup.select("div[class*='vehicle-card']")
                    )
                    for card in cards:
                        try:
                            full_text = card.get_text(" ", strip=True)
                            model = _detect_model(full_text)
                            if not model:
                                continue
                            link_el = card.select_one("a[href]")
                            href = link_el["href"] if link_el else ""
                            if href and not href.startswith("http"):
                                href = self.BASE_URL + href
                            price_el = card.select_one("[class*='price']")
                            price_sek = int(re.sub(r"[^\d]", "", price_el.get_text()) if price_el else "0") or 0
                            year = 0
                            m = re.search(r"(20\d{2}|19\d{2})", full_text)
                            if m:
                                year = int(m.group(1))
                            mileage_mil = 0.0
                            m = re.search(r"(\d[\d\s]*)\s*mil", full_text, re.IGNORECASE)
                            if m:
                                raw_mil = re.sub(r"[^\d,.]", "", m.group(0)).replace(",", ".")
                                mileage_mil = float(raw_mil) if raw_mil else 0.0
                            fuel = _detect_fuel(full_text)
                            features, audio_system = _extract_features([], full_text)
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
                                "title": f"Volvo {model} {year}",
                            })
                        except Exception:
                            pass
                    continue

                # API response item
                make = str(item.get("make") or item.get("brand") or "Volvo")
                if "volvo" not in make.lower():
                    continue

                model_raw = str(item.get("model") or item.get("modelName") or item.get("series") or "")
                model = _detect_model(model_raw)
                if not model:
                    model = _detect_model(str(item.get("title") or ""))
                if not model:
                    continue

                price_sek = int(item.get("price") or item.get("sellingPrice") or item.get("priceValue") or 0)
                year = int(item.get("modelYear") or item.get("year") or 0)

                # Mileage: could be in km or mil
                mileage_raw = float(item.get("mileage") or item.get("odometer") or 0)
                # Volvo Selekt typically uses km
                mileage_mil = mileage_raw / 10.0

                fuel_raw = str(item.get("fuelType") or item.get("fuel") or item.get("engineType") or "")
                fuel = _detect_fuel(fuel_raw)

                equipment = item.get("equipment") or item.get("features") or item.get("options") or []
                description = str(item.get("description") or "")
                features, audio_system = _extract_features(equipment, description)

                vin = item.get("vin") or item.get("registrationNumber")
                color = item.get("color") or item.get("exteriorColor")
                gearbox = item.get("gearbox") or item.get("transmission")

                car_id = item.get("id") or item.get("vehicleId") or ""
                url = (
                    item.get("url")
                    or item.get("shareUrl")
                    or item.get("detailUrl")
                    or f"{self.BASE_URL}/se/cars/{car_id}"
                )
                title = str(item.get("title") or f"Volvo {model} {year}")

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
