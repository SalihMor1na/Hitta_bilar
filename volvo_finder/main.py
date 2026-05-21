"""Main entry point for the Volvo Finder scraper system."""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Ensure the package root (volvo_finder/) is on path when run as a module
_PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

from rich.console import Console
from rich.table import Table
from rich import box

from config.settings import settings
from models.car import Car
from scrapers.blocket_scraper import BlocketScraper
from scrapers.bytbil_scraper import BytbilScraper
from scrapers.wayke_scraper import WaykeScraper
from scrapers.kvdbil_scraper import KvdbilScraper
from scrapers.bilweb_scraper import BilwebScraper
from scrapers.bilhall_scraper import BilhallScraper
from scrapers.volvo_selekt_scraper import VolvoSelektScraper
from scrapers.hedin_scraper import HedinScraper
from scrapers.bilia_scraper import BiliaScraper
from services.filter_service import FilterService
from services.ranking_service import RankingService
from services.deduplication_service import DeduplicationService
from services.notification_service import NotificationService
from repositories.car_repository import CarRepository
from utils.http_client import HttpClient
from utils.cache import Cache

console = Console()


def setup_logging() -> None:
    """Configure structured JSON-compatible logging."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def display_table(cars: list[Car]) -> None:
    """Display top N cars in a Rich table on the CLI."""
    if not cars:
        console.print("[yellow]No cars found matching criteria.[/yellow]")
        return

    table = Table(
        title=f"Top {len(cars)} Volvo Cars Found",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Model", style="bold white", min_width=6)
    table.add_column("Year", style="cyan", width=6)
    table.add_column("Price (SEK)", style="green", min_width=12)
    table.add_column("Mileage (mil)", style="yellow", min_width=13)
    table.add_column("Fuel", style="magenta", min_width=10)
    table.add_column("Score", style="bold green", width=7)
    table.add_column("Source", style="dim", min_width=10)
    table.add_column("Features", style="dim", min_width=20)

    for i, car in enumerate(cars, 1):
        features_str = ", ".join(sorted(car.features)[:4])
        if len(car.features) > 4:
            features_str += "..."
        table.add_row(
            str(i),
            car.model,
            str(car.year),
            f"{car.price_sek:,}",
            f"{car.mileage_mil:,.0f}",
            car.fuel,
            f"{car.score:.1f}",
            car.source,
            features_str,
        )

    console.print(table)


def generate_report(cars: list[Car], previous: list[Car]) -> None:
    """Generate a Markdown report with new, gone, and price-drop cars."""
    report_path = Path("volvo_report.md")

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    prev_by_url: dict[str, Car] = {c.url: c for c in previous}
    prev_by_vin: dict[str, Car] = {c.vin: c for c in previous if c.vin}

    curr_by_url: dict[str, Car] = {c.url: c for c in cars}
    curr_by_vin: dict[str, Car] = {c.vin: c for c in cars if c.vin}

    def get_prev(car: Car) -> Optional[Car]:
        if car.vin and car.vin in prev_by_vin:
            return prev_by_vin[car.vin]
        return prev_by_url.get(car.url)

    def is_new(car: Car) -> bool:
        return get_prev(car) is None

    def in_current(prev_car: Car) -> bool:
        if prev_car.url in curr_by_url:
            return True
        if prev_car.vin and prev_car.vin in curr_by_vin:
            return True
        return False

    new_cars = [c for c in cars if is_new(c)]
    gone_cars = [c for c in previous if not in_current(c)]
    price_drops = [
        (c, get_prev(c))
        for c in cars
        if get_prev(c) is not None and c.price_sek < get_prev(c).price_sek
    ]

    lines = [
        f"# Volvo Finder Report",
        f"",
        f"Generated: {now_str}",
        f"",
        f"**Total listings after filter & dedup:** {len(cars)}",
        f"**New since last run:** {len(new_cars)}",
        f"**Gone since last run:** {len(gone_cars)}",
        f"**Price drops:** {len(price_drops)}",
        f"",
    ]

    # New cars
    if new_cars:
        lines.append("## 🆕 New Cars")
        lines.append("")
        lines.append("| Model | Year | Price | Mileage | Fuel | Source | Link |")
        lines.append("|-------|------|-------|---------|------|--------|------|")
        for car in new_cars:
            lines.append(
                f"| {car.model} | {car.year} | {car.price_sek:,} | "
                f"{car.mileage_mil:.0f} | {car.fuel} | {car.source} | "
                f"[link]({car.url}) |"
            )
        lines.append("")

    # Gone cars
    if gone_cars:
        lines.append("## ❌ Gone Cars")
        lines.append("")
        lines.append("| Model | Year | Price | Source |")
        lines.append("|-------|------|-------|--------|")
        for car in gone_cars:
            lines.append(f"| {car.model} | {car.year} | {car.price_sek:,} | {car.source} |")
        lines.append("")

    # Price drops
    if price_drops:
        lines.append("## ⬇️ Price Drops")
        lines.append("")
        lines.append("| Model | Year | Old Price | New Price | Drop | Source | Link |")
        lines.append("|-------|------|-----------|-----------|------|--------|------|")
        for car, prev_car in price_drops:
            drop = prev_car.price_sek - car.price_sek
            lines.append(
                f"| {car.model} | {car.year} | {prev_car.price_sek:,} | "
                f"{car.price_sek:,} | -{drop:,} | {car.source} | [link]({car.url}) |"
            )
        lines.append("")

    # Top ranked cars
    lines.append("## 🏆 Top Ranked Cars")
    lines.append("")
    lines.append("| # | Model | Year | Price | Mileage | Fuel | Score | Source | Link |")
    lines.append("|---|-------|------|-------|---------|------|-------|--------|------|")
    for i, car in enumerate(cars[:20], 1):
        lines.append(
            f"| {i} | {car.model} | {car.year} | {car.price_sek:,} | "
            f"{car.mileage_mil:.0f} | {car.fuel} | {car.score:.1f} | "
            f"{car.source} | [link]({car.url}) |"
        )

    report_path.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[green]Report written to {report_path.absolute()}[/green]")


async def main() -> None:
    """Main async entry point."""
    setup_logging()
    logger = logging.getLogger("main")

    logger.info(json.dumps({"event": "run_start", "time": datetime.utcnow().isoformat()}))

    async with HttpClient(settings) as http_client:
        cache = Cache(settings)
        repo = CarRepository(settings)

        scrapers = [
            BlocketScraper(http_client, cache),
            BytbilScraper(http_client, cache),
            WaykeScraper(http_client, cache),
            KvdbilScraper(http_client, cache),
            BilwebScraper(http_client, cache),
            BilhallScraper(http_client, cache),
            VolvoSelektScraper(http_client, cache),
            HedinScraper(http_client, cache),
            BiliaScraper(http_client, cache),
        ]

        console.print(f"[bold cyan]VolvoFinder[/bold cyan] — running {len(scrapers)} scrapers...")

        # Run all scrapers concurrently
        results = await asyncio.gather(
            *[s.scrape() for s in scrapers],
            return_exceptions=True,
        )

        all_cars: list[Car] = []
        for i, result in enumerate(results):
            scraper_name = scrapers[i].NAME
            if isinstance(result, Exception):
                logger.error(json.dumps({
                    "event": "scraper_exception",
                    "scraper": scraper_name,
                    "error": str(result),
                }))
            elif isinstance(result, list):
                console.print(f"  [dim]{scraper_name}[/dim]: {len(result)} listings")
                all_cars.extend(result)
            else:
                logger.warning(json.dumps({
                    "event": "unexpected_result_type",
                    "scraper": scraper_name,
                }))

        console.print(f"\n[bold]Total raw listings:[/bold] {len(all_cars)}")

        # Filter
        filter_svc = FilterService()
        filtered = filter_svc.filter(all_cars)
        console.print(f"[bold]After hard filters:[/bold] {len(filtered)}")

        # Deduplicate
        dedup_svc = DeduplicationService()
        unique = dedup_svc.deduplicate(filtered)
        console.print(f"[bold]After deduplication:[/bold] {len(unique)}")

        # Rank
        ranking_svc = RankingService()
        ranked = ranking_svc.rank(unique)

        # Get previous run for diff
        previous = repo.get_latest_run_cars()

        # Save to DB
        if ranked:
            repo.save_cars(ranked)

        # Display CLI table (top 20)
        display_table(ranked[:20])

        # Generate Markdown report
        generate_report(ranked, previous)

        # Notifications
        notify_svc = NotificationService(settings)
        prev_urls = {c.url for c in previous}
        prev_vins = {c.vin for c in previous if c.vin}
        new_cars = [
            c for c in ranked
            if c.url not in prev_urls and (not c.vin or c.vin not in prev_vins)
        ]

        if new_cars:
            console.print(f"[bold green]Found {len(new_cars)} new car(s) — sending notifications...[/bold green]")
            await notify_svc.notify_new_cars(new_cars)

        logger.info(json.dumps({
            "event": "run_complete",
            "total_raw": len(all_cars),
            "filtered": len(filtered),
            "unique": len(unique),
            "new": len(new_cars),
        }))


if __name__ == "__main__":
    asyncio.run(main())
