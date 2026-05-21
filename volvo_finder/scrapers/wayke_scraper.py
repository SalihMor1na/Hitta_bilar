"""Wayke.se scraper using public HTML search pages."""
import json
import re
from typing import Optional

from bs4 import BeautifulSoup

from models.car import Car
from scrapers.base_scraper import BaseScraper
from utils.cache import Cache
from utils.http_client import HttpClient
from utils.nextjs import extract_next_data, find_listings_in_next_data

# Public search URL — same as any browser
_BASE_SEARCH_URL = "https://www.wayke.se/hitta-bil"
_MODELS = ["v60", "v90", "xc60"]
_WARMUP_URL = "https://www.wayke.se/"
_SEARCH_PARAMS = {
    "makes": "Volvo",
    "priceMax": "300000",
    "yearMin": "2018",
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


class WaykeScraper(BaseScraper):
    NAME = "wayke"
    BASE_URL = "https://www.wayke.se"

    def __init__(self, http_client: HttpClient, cache: Cache) -> None:
        super().__init__(http_client, cache)

    async def fetch(self) -> list[dict]:
        """Fetch Volvo listings from Wayke using public HTML search."""
        await self.http_client.warm_up(_WARMUP_URL)

        all_items: list[dict] = []

        for model in _MODELS:
            page = 1
            while page <= 10:
                params = {**_SEARCH_PARAMS, "models": model.upper(), "page": str(page)}
                cache_key = f"wayke:{model}:page{page}"
                cached = self.cache.get(cache_key)
                if cached is not None:
                    html = cached
                else:
                    html = await self.http_client.get_text(_BASE_SEARCH_URL, params=params)
                    if html:
                        self.cache.set(cache_key, html)

                if not html:
                    self.logger.warning(json.dumps({
                        "event": "wayke_fetch_empty", "model": model, "page": page,
                    }))
                    break

                # Try __NEXT_DATA__ first
                next_data = None
                try:
                    next_data = extract_next_data(html)
                except Exception:
                    pass

                if next_data:
                    listings = find_listings_in_next_data(next_data)
                    if listings:
                        all_items.extend(listings)
                        if len(listings) < 20:
                            break
                        page += 1
                        continue
                    self.logger.info(json.dumps({
                        "event": "wayke_no_listings_in_next_data", "model": model,
                    }))

                # Fall back to HTML card parsing
                soup = BeautifulSoup(html, "html.parser")
                cards = (
                    soup.select("[class*='vehicle-card']")
                    or soup.select("[class*='car-card']")
                    or soup.select("article[class*='vehicle']")
                    or soup.select("[data-testid*='vehicle']")
                    or soup.select("[data-testid*='car']")
                    or soup.select("li.hit")
                )
                if not cards:
                    self.logger.info(json.dumps({
                        "event": "wayke_no_cards", "model": model, "page": page,
                        "html_snippet": soup.body.get_text(" ", strip=True)[:300] if soup.body else "",
                    }))
                    break

                for card in cards:
                    all_items.append({"_html": str(card), "_base_url": self.BASE_URL})

                next_btn = soup.select_one("a[rel='next']") or soup.select_one("[aria-label*='nästa']")
                if not next_btn:
                    break
                page += 1

        return all_items

    def parse(self, raw_data: list[dict]) -> list[dict]:
        parsed = []
        for item in raw_data:
            try:
                # HTML card fallback
                if item.get("_html"):
                    soup = BeautifulSoup(item["_html"], "html.parser")
                    base_url = item.get("_base_url", self.BASE_URL)
                    full_text = soup.get_text(" ", strip=True)
                    model = None
                    for m in ["V60", "V90", "XC60"]:
                        if m.lower() in full_text.lower():
                            model = m
                            break
                    if not model:
                        continue
                    link_el = soup.select_one("a[href]")
                    href = link_el["href"] if link_el else ""
                    if href and not href.startswith("http"):
                        href = base_url + href
                    price_el = soup.select_one("[class*='price']")
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
                    continue

                # Wayke vehicle fields (from __NEXT_DATA__)
                make = str(item.get("make") or item.get("manufacturer") or "")
                if "volvo" not in make.lower():
                    continue

                model_raw = str(item.get("model") or item.get("modelName") or "")
                model = None
                for m in ["V60", "V90", "XC60"]:
                    if m.lower() in model_raw.lower():
                        model = m
                        break
                if not model:
                    continue

                price_sek = int(item.get("price") or item.get("sellingPrice") or 0)
                year = int(item.get("modelYear") or item.get("year") or 0)

                mileage_raw = item.get("mileage") or item.get("odometer") or 0
                mileage_mil = float(mileage_raw) / 10.0

                fuel = _detect_fuel(str(item.get("fuelType") or item.get("fuel") or ""))
                color = item.get("color") or item.get("exteriorColor")
                gearbox = item.get("gearbox") or item.get("transmission")
                vin = item.get("vin") or item.get("registrationNumber")

                equipment = item.get("equipment") or item.get("features") or []
                equip_text = " ".join(str(e) for e in equipment) if isinstance(equipment, list) else str(equipment)
                description = str(item.get("description") or "")
                features, audio_system = _extract_features(f"{equip_text} {description}")

                url = (
                    item.get("url")
                    or item.get("shareUrl")
                    or f"https://www.wayke.se/bil/{item.get('id', '')}"
                )

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
                    "title": f"Volvo {model} {year}",
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
