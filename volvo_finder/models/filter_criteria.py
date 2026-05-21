"""Filter criteria for hard-filtering car listings."""
from dataclasses import dataclass, field


@dataclass
class FilterCriteria:
    """
    Hard filter criteria for Volvo car search.

    These are class-level constants that define what cars are allowed.
    Note: MAX_MILEAGE_DIESEL_MIL = 16000 mil (Swedish mil, 1 mil = 10 km → 160 000 km).
    """
    ALLOWED_MODELS: frozenset = field(
        default_factory=lambda: frozenset({"V60", "V90", "XC60"})
    )
    MIN_YEAR: int = 2018
    MAX_PRICE_SEK: int = 300_000

    # NOTE: 16 000 Swedish mil = 160 000 km — kept as per specification
    MAX_MILEAGE_DIESEL_MIL: float = 16_000.0
    MAX_MILEAGE_RECHARGE_MIL: float = 11_000.0

    # Feature requirements (special logic: backkamera OR 360)
    REQUIRED_FEATURES: frozenset = field(
        default_factory=lambda: frozenset({"panoramatak", "backkamera_or_360"})
    )
    REQUIRED_AUDIO_EQUIPMENT: frozenset = field(
        default_factory=lambda: frozenset({"skinnstolar", "blis"})
    )
    ALLOWED_FUELS: frozenset = field(
        default_factory=lambda: frozenset({"diesel", "recharge"})
    )
