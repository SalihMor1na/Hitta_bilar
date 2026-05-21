"""Car data models."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Car:
    """Represents a single used Volvo car listing."""
    source: str
    url: str
    model: str          # "V60", "V90", "XC60"
    year: int
    price_sek: int
    mileage_mil: float
    fuel: str           # "diesel", "recharge", "bensin", etc.
    features: set[str] = field(default_factory=set)
    audio_system: Optional[str] = None
    vin: Optional[str] = None
    score: float = 0.0
    title: Optional[str] = None
    color: Optional[str] = None
    gearbox: Optional[str] = None
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None

    def __hash__(self) -> int:
        return hash(self.url)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Car):
            return NotImplemented
        return self.url == other.url


@dataclass
class CarHistory:
    """Tracks price and mileage history for a car over time."""
    vin_or_id: str
    timestamp: datetime
    price_sek: int
    mileage_mil: float
