"""Blocket.se scraper using Playwright for JS-rendered search results."""
import asyncio
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
from utils.nextjs import extract_next_data

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
                cache_key = f"playwright:blocket:{model}:page{page}"
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
                price_text = price_el.get_text(strip=True) if price_el else ""
                price_sek = int(re.sub(r"[^\d]", "", price_text) or "0")
                if not price_sek:
                    # CSS class names are minified — fall back to text regex "229 000 kr"
                    m_p = re.search(r"(\d[\d\s]{2,})\s*kr", full_text)
                    if m_p:
                        price_sek = int(re.sub(r"[^\d]", "", m_p.group(1)) or "0")

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

    async def enrich(self, cars: list[Car]) -> list[Car]:
        """
        Fetch each Blocket listing's detail page to get the equipment list.
        Blocket search cards don't include equipment — only the detail page does.
        Uses HTTP first (SSR pages); falls back to Playwright if needed.
        Runs up to 5 fetches concurrently. Results are cached 6h.
        """
        sem = asyncio.Semaphore(5)

        async def _enrich_one(car: Car) -> Car:
            if not car.url:
                return car
            async with sem:
                cache_key = f"blocket:detail:{car.url}"
                html = self.cache.get(cache_key)
                if not html:
                    html = await self.http_client.get_text(car.url)
                    if html:
                        self.cache.set(cache_key, html)
                # Always mark enrichment attempted so the filter knows to be strict
                car.features.add("enrichment_done")
                if not html:
                    return car
                features, audio, price, fuel = self._parse_detail_page(html)
                car.features |= features
                if audio and not car.audio_system:
                    car.audio_system = audio
                if price and not car.price_sek:
                    car.price_sek = price
                if fuel and car.fuel == "okänd":
                    car.fuel = fuel
                self.logger.debug(json.dumps({
                    "event": "blocket_enriched",
                    "url": car.url,
                    "features": sorted(car.features - {"enrichment_done"}),
                    "price": car.price_sek,
                }))
            return car

        return list(await asyncio.gather(*[_enrich_one(c) for c in cars]))

    def _parse_detail_page(self, html: str) -> tuple[set[str], Optional[str], int, str]:
        """
        Extract features, audio system, price, and fuel from a Blocket detail page.
        Returns (features, audio_system, price_sek, fuel).
        """
        features: set[str] = set()
        audio_system: Optional[str] = None
        price_sek: int = 0
        fuel: str = "okänd"

        # Try __NEXT_DATA__ first (Blocket detail pages are Next.js SSR)
        next_data = extract_next_data(html)
        ad = self._find_ad_in_next_data(next_data) if next_data else None

        if ad:
            full_text = self._ad_to_text(ad)
            features, audio_system = _extract_features_from_text(full_text)
            fuel = _detect_fuel(full_text)

            # Price from structured data
            price_raw = ad.get("price", {})
            if isinstance(price_raw, dict):
                price_sek = int(price_raw.get("value", 0) or price_raw.get("amount", 0) or 0)
            elif isinstance(price_raw, (int, float)):
                price_sek = int(price_raw)
        else:
            # Fall back to full-page text scan
            soup = BeautifulSoup(html, "html.parser")
            equip_section = soup.find(
                lambda tag: tag.name in ("section", "div", "ul")
                and "utrustning" in (tag.get_text() or "").lower()
            )
            text = equip_section.get_text(" ", strip=True) if equip_section else soup.get_text(" ", strip=True)
            features, audio_system = _extract_features_from_text(text)
            fuel = _detect_fuel(text)
            # Price regex: "229 000 kr"
            m = re.search(r"(\d[\d\s]{2,})\s*kr", text)
            if m:
                price_sek = int(re.sub(r"[^\d]", "", m.group(1)) or "0")

        return features, audio_system, price_sek, fuel

    def _find_ad_in_next_data(self, data: dict) -> Optional[dict]:
        """Locate the ad/listing dict in Blocket __NEXT_DATA__."""
        for path_fn in [
            lambda d: d["props"]["pageProps"]["ad"],
            lambda d: d["props"]["pageProps"]["listing"],
            lambda d: d["props"]["pageProps"]["data"],
            lambda d: d["props"]["pageProps"]["initialData"]["ad"],
        ]:
            try:
                result = path_fn(data)
                if isinstance(result, dict):
                    return result
            except (KeyError, TypeError):
                pass
        return None

    def _ad_to_text(self, ad: dict) -> str:
        """Flatten a Blocket ad dict into a single searchable text blob."""
        parts = [
            str(ad.get("subject", "")),
            str(ad.get("body", "") or ad.get("description", "")),
        ]
        for p in ad.get("parameters", []) or []:
            parts.append(f"{p.get('label', '')} {p.get('value', '')}")
        return " ".join(parts)
