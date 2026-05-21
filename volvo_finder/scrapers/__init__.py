"""Scrapers package."""
from scrapers.base_scraper import BaseScraper
from scrapers.blocket_scraper import BlocketScraper
from scrapers.bytbil_scraper import BytbilScraper
from scrapers.wayke_scraper import WaykeScraper
from scrapers.kvdbil_scraper import KvdbilScraper
from scrapers.bilweb_scraper import BilwebScraper
from scrapers.bilhall_scraper import BilhallScraper
from scrapers.volvo_selekt_scraper import VolvoSelektScraper
from scrapers.hedin_scraper import HedinScraper
from scrapers.bilia_scraper import BiliaScraper

__all__ = [
    "BaseScraper",
    "BlocketScraper",
    "BytbilScraper",
    "WaykeScraper",
    "KvdbilScraper",
    "BilwebScraper",
    "BilhallScraper",
    "VolvoSelektScraper",
    "HedinScraper",
    "BiliaScraper",
]
