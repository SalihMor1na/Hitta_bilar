"""SQLite repository for storing and retrieving car listings."""
import json
import logging
import sqlite3
from datetime import datetime
from typing import Any, Optional

from models.car import Car, CarHistory

logger = logging.getLogger(__name__)

_CREATE_CARS_SQL = """
CREATE TABLE IF NOT EXISTS cars (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT NOT NULL UNIQUE,
    vin         TEXT,
    source      TEXT NOT NULL,
    model       TEXT NOT NULL,
    year        INTEGER NOT NULL,
    price_sek   INTEGER NOT NULL,
    mileage_mil REAL NOT NULL,
    fuel        TEXT NOT NULL,
    features    TEXT NOT NULL DEFAULT '[]',
    audio_system TEXT,
    score       REAL NOT NULL DEFAULT 0.0,
    title       TEXT,
    color       TEXT,
    gearbox     TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
)
"""

_CREATE_HISTORY_SQL = """
CREATE TABLE IF NOT EXISTS car_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    car_url     TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    price_sek   INTEGER NOT NULL,
    mileage_mil REAL NOT NULL
)
"""

_CREATE_RUNS_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at      TEXT NOT NULL
)
"""

_CREATE_RUN_CARS_SQL = """
CREATE TABLE IF NOT EXISTS run_cars (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  INTEGER NOT NULL,
    car_url TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id)
)
"""


def _car_to_row(car: Car, now_str: str, first_seen: Optional[str] = None) -> dict:
    return {
        "url": car.url,
        "vin": car.vin,
        "source": car.source,
        "model": car.model,
        "year": car.year,
        "price_sek": car.price_sek,
        "mileage_mil": car.mileage_mil,
        "fuel": car.fuel,
        "features": json.dumps(sorted(car.features)),
        "audio_system": car.audio_system,
        "score": car.score,
        "title": car.title,
        "color": car.color,
        "gearbox": car.gearbox,
        "first_seen": first_seen or now_str,
        "last_seen": now_str,
    }


def _row_to_car(row: dict) -> Car:
    features_raw = row.get("features", "[]")
    try:
        features = set(json.loads(features_raw))
    except (json.JSONDecodeError, TypeError):
        features = set()

    first_seen = None
    last_seen = None
    try:
        if row.get("first_seen"):
            first_seen = datetime.fromisoformat(row["first_seen"])
        if row.get("last_seen"):
            last_seen = datetime.fromisoformat(row["last_seen"])
    except ValueError:
        pass

    return Car(
        source=row["source"],
        url=row["url"],
        model=row["model"],
        year=row["year"],
        price_sek=row["price_sek"],
        mileage_mil=row["mileage_mil"],
        fuel=row["fuel"],
        features=features,
        audio_system=row.get("audio_system"),
        vin=row.get("vin"),
        score=row.get("score", 0.0),
        title=row.get("title"),
        color=row.get("color"),
        gearbox=row.get("gearbox"),
        first_seen=first_seen,
        last_seen=last_seen,
    )


class CarRepository:
    """SQLite-backed repository for car listings and history."""

    def __init__(self, settings: Any) -> None:
        # Extract path from DATABASE_URL (sqlite:///path or sqlite:////abs)
        db_url = settings.DATABASE_URL
        if db_url.startswith("sqlite:///"):
            self.db_path = db_url[len("sqlite:///"):]
        else:
            self.db_path = db_url
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        with self._connect() as conn:
            conn.execute(_CREATE_CARS_SQL)
            conn.execute(_CREATE_HISTORY_SQL)
            conn.execute(_CREATE_RUNS_SQL)
            conn.execute(_CREATE_RUN_CARS_SQL)
            conn.commit()

    def save_cars(self, cars: list[Car]) -> None:
        """
        Upsert cars by URL. Updates last_seen and price/mileage.
        Also records this as a new run.
        """
        now_str = datetime.utcnow().isoformat()
        with self._connect() as conn:
            # Create a new run record
            cur = conn.execute("INSERT INTO runs (run_at) VALUES (?)", (now_str,))
            run_id = cur.lastrowid

            for car in cars:
                # Check if car already exists
                existing = conn.execute(
                    "SELECT id, first_seen FROM cars WHERE url = ?",
                    (car.url,)
                ).fetchone()

                row = _car_to_row(car, now_str, first_seen=existing["first_seen"] if existing else None)

                if existing:
                    conn.execute("""
                        UPDATE cars SET
                            vin=:vin, source=:source, model=:model, year=:year,
                            price_sek=:price_sek, mileage_mil=:mileage_mil, fuel=:fuel,
                            features=:features, audio_system=:audio_system, score=:score,
                            title=:title, color=:color, gearbox=:gearbox, last_seen=:last_seen
                        WHERE url=:url
                    """, row)
                else:
                    conn.execute("""
                        INSERT INTO cars
                            (url, vin, source, model, year, price_sek, mileage_mil, fuel,
                             features, audio_system, score, title, color, gearbox,
                             first_seen, last_seen)
                        VALUES
                            (:url, :vin, :source, :model, :year, :price_sek, :mileage_mil,
                             :fuel, :features, :audio_system, :score, :title, :color,
                             :gearbox, :first_seen, :last_seen)
                    """, row)

                # Record in run_cars
                conn.execute(
                    "INSERT INTO run_cars (run_id, car_url) VALUES (?, ?)",
                    (run_id, car.url)
                )

                # Append to history
                conn.execute("""
                    INSERT INTO car_history (car_url, timestamp, price_sek, mileage_mil)
                    VALUES (?, ?, ?, ?)
                """, (car.url, now_str, car.price_sek, car.mileage_mil))

            conn.commit()
        logger.info(json.dumps({"event": "cars_saved", "count": len(cars), "run_id": run_id}))

    def get_previous_cars(self) -> list[Car]:
        """Get all cars stored in the database (from all previous runs)."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM cars ORDER BY last_seen DESC").fetchall()
        return [_row_to_car(dict(row)) for row in rows]

    def get_latest_run_cars(self) -> list[Car]:
        """Get cars from the most recent run."""
        with self._connect() as conn:
            latest_run = conn.execute(
                "SELECT id FROM runs ORDER BY run_at DESC LIMIT 1"
            ).fetchone()
            if not latest_run:
                return []
            run_id = latest_run["id"]
            rows = conn.execute("""
                SELECT c.* FROM cars c
                JOIN run_cars rc ON rc.car_url = c.url
                WHERE rc.run_id = ?
            """, (run_id,)).fetchall()
        return [_row_to_car(dict(row)) for row in rows]

    def save_history(self, car: Car) -> None:
        """Append a price/mileage snapshot to car_history."""
        now_str = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO car_history (car_url, timestamp, price_sek, mileage_mil)
                VALUES (?, ?, ?, ?)
            """, (car.url, now_str, car.price_sek, car.mileage_mil))
            conn.commit()

    def get_history(self, url: str) -> list[CarHistory]:
        """Get price/mileage history for a car by URL."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM car_history WHERE car_url = ? ORDER BY timestamp",
                (url,)
            ).fetchall()
        result = []
        for row in rows:
            try:
                ts = datetime.fromisoformat(row["timestamp"])
            except (ValueError, KeyError):
                ts = datetime.utcnow()
            result.append(CarHistory(
                vin_or_id=url,
                timestamp=ts,
                price_sek=row["price_sek"],
                mileage_mil=row["mileage_mil"],
            ))
        return result
