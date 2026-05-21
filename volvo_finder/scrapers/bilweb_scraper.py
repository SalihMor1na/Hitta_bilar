"""Bilweb.se scraper — JSON API endpoint verified in browser."""
import json
import re
from typing import Optional

from bs4 import BeautifulSoup

from models.car import Car
from scrapers.base_scraper import BaseScraper
from utils.cache import Cache
from utils.http_client import HttpClient

# Correct URL verified by user in Chrome (looks like a JSON API)
_SEARCH_URL = "https://bilweb.se/sok"
_WARMUP_URL = "https://bilweb.se/"
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
        """Fetch Bilweb listings — tries JSON API first, falls back to HTML."""
        await self.http_client.warm_up(_WARMUP_URL)
        all_items: list[dict] = []
        limit = 30
        offset = 0

        while True:
            params = {**_BASE_PARAMS, "offset": str(offset)}
            cache_key = f"bilweb:offset{offset}"
            cached = self.cache.get(cache_key)

            if cached is not None:
                try:
                    data = json.loads(cached)
                except json.JSONDecodeError:
                    data = None
                    html = cached
            else:
                # Try JSON first
                data = await self.http_client.get_json(_SEARCH_URL, params=params)
                if data is not None:
                    self.cache.set(cache_key, json.dumps(data))
                else:
                    # Fall back to HTML
                    html = await self.http_client.get_text(_SEARCH_URL, params=params)
                    if html:
                        self.cache.set(cache_key, html)
                    data = None

            if data is not None:
                # JSON response
                items = (
                    data.get("ads") or data.get("items") or data.get("listings")
                    or data.get("data") or data.get("vehicles")
                    or (data if isinstance(data, list) else [])
                )
                if not items:
                    self.logger.info(json.dumps({
                        "event": "bilweb_no_json_items", "offset": offset,
                        "keys": list(data.keys()) if isinstance(data, dict) else "list",
                    }))
                    break
                all_items.extend(items if isinstance(items, list) else [])
                if len(items) < limit:
                    break
                offset += limit
            else:
                # HTML response
                if not html:
                    self.logger.warning(json.dumps({"event": "fetch_empty", "scraper": self.NAME, "offset": offset}))
                    break
                all_items.append({"_html": html, "_base_url": self.BASE_URL})
                break  # HTML has no pagination info, fetch once

            if offset > 300:  # safety cap
                break

        return all_items

    def parse(self, raw_data: list[dict]) -> list[dict]:
        parsed = []
        for item in raw_data:
            try:
                # HTML fallback
                if item.get("_html"):
                    soup = BeautifulSoup(item["_html"], "html.parser")
                    base_url = item.get("_base_url", self.BASE_URL)
                    cards = (
                        soup.select("div.car-item") or soup.select("article.car")
                        or soup.select("[class*='car-card']") or soup.select("li.search-item")
                        or soup.select("div[class*='listing']")
                    )
                    if not cards:
                        self.logger.info(json.dumps({
                            "event": "no_cards_found", "scraper": self.NAME,
                            "html_snippet": soup.body.get_text(" ", strip=True)[:300] if soup.body else "",
                        }))
                    for card in cards:
                        try:
                            full_text = card.get_text(" ", strip=True)
                            title_el = card.select_one("h2") or card.select_one("h3") or card.select_one("[class*='title']")
                            title = title_el.get_text(strip=True) if title_el else full_text[:80]
                            model = _detect_model(title) or _detect_model(full_text)
                            if not model:
                                continue
                            link_el = card.select_one("a[href]")
                            href = link_el["href"] if link_el else ""
                            if href and not href.startswith("http"):
                                href = base_url + href
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
                                "source": self.NAME, "url": href, "model": model,
                                "year": year, "price_sek": price_sek, "mileage_mil": mileage_mil,
                                "fuel": fuel, "features": features, "audio_system": audio_system, "title": title,
                            })
                        except Exception:
                            pass
                    continue

                # JSON item
                headline = str(item.get("headline") or item.get("title") or item.get("subject") or "")
                model = _detect_model(headline)
                if not model:
                    for key in ("brand_name", "brand", "make"):
                        brand = str(item.get(key) or "").lower()
                        if "volvo" in brand:
                            break
                    else:
                        continue
                    model = _detect_model(str(item.get("model_name") or item.get("model") or ""))
                    if not model:
                        continue

                price_sek = int(item.get("price") or item.get("price_value") or 0)
                year = int(item.get("year") or item.get("model_year") or 0)

                # Bilweb mileage: property_mileage is in km, convert to mil
                mileage_km = float(item.get("property_mileage") or item.get("mileage") or 0)
                mileage_mil = mileage_km / 10.0

                fuel = _detect_fuel(str(item.get("fuel_name") or item.get("fuel") or ""))
                description = str(item.get("description") or item.get("body") or "")
                features, audio_system = _extract_features(description)

                ad_id = item.get("id") or item.get("ad_id") or ""
                url = item.get("url") or item.get("share_url") or f"{self.BASE_URL}/annons/{ad_id}"

                parsed.append({
                    "source": self.NAME, "url": url, "model": model,
                    "year": year, "price_sek": price_sek, "mileage_mil": mileage_mil,
                    "fuel": fuel, "features": features, "audio_system": audio_system,
                    "title": headline or f"Volvo {model} {year}",
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
