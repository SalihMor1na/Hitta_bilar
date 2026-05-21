"""Utilities for parsing Next.js __NEXT_DATA__ embedded JSON."""
import json
from typing import Any, Optional

from bs4 import BeautifulSoup


def extract_next_data(html: str) -> Optional[dict]:
    """Extract and parse the __NEXT_DATA__ JSON from a Next.js page."""
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return None
    try:
        return json.loads(script.string)
    except json.JSONDecodeError:
        return None


def find_listings_in_next_data(data: dict, min_count: int = 1) -> list[dict]:
    """
    Recursively search for a list of car-listing-like dicts in Next.js data.
    Returns the first list found that looks like car listings (has 'price' or 'subject' keys).
    """
    def _search(obj: Any, depth: int = 0) -> Optional[list]:
        if depth > 8:
            return None
        if isinstance(obj, list) and len(obj) >= min_count:
            # Check if items look like car listings
            if obj and isinstance(obj[0], dict):
                keys = set(obj[0].keys())
                car_indicators = {"price", "subject", "title", "parameters", "mileage", "year", "make"}
                if keys & car_indicators:
                    return obj
        if isinstance(obj, dict):
            for v in obj.values():
                result = _search(v, depth + 1)
                if result is not None:
                    return result
        if isinstance(obj, list):
            for item in obj:
                result = _search(item, depth + 1)
                if result is not None:
                    return result
        return None

    return _search(data) or []
