"""Filter service for hard-filtering car listings."""
from models.car import Car
from models.filter_criteria import FilterCriteria


class FilterService:
    """
    Applies hard filter criteria to car listings.

    Required criteria:
    - Model: V60, V90, or XC60
    - Year: >= 2018
    - Price: <= 300 000 SEK
    - Fuel: diesel (max 16 000 mil) or recharge/PHEV/laddhybrid (max 11 000 mil)
    - Features: panoramatak + (backkamera OR 360kamera) + skinnstolar + blis
    """

    def __init__(self, criteria: FilterCriteria = None) -> None:
        self.criteria = criteria or FilterCriteria()

    def is_allowed_fuel(self, car: Car) -> bool:
        """
        Diesel: max 16 000 mil.
        Recharge/PHEV/laddhybrid: max 11 000 mil.
        Unknown fuel ("okänd"): allowed through — search cards often omit fuel.
        All other known fuels (bensin, el, hybrid): excluded.
        """
        fuel = car.fuel.lower()
        if fuel == "okänd":
            return True  # Can't filter on unknown data
        if "diesel" in fuel:
            mil = car.mileage_mil
            return mil == 0 or mil <= self.criteria.MAX_MILEAGE_DIESEL_MIL
        elif "recharge" in fuel or "phev" in fuel or "laddhybrid" in fuel:
            mil = car.mileage_mil
            return mil == 0 or mil <= self.criteria.MAX_MILEAGE_RECHARGE_MIL
        return False

    def has_required_features(self, car: Car) -> bool:
        """
        Must have: panoramatak, backkamera/360kamera, skinnstolar, blis.
        If features set is empty (search cards don't list equipment), allow through.
        """
        if not car.features:
            return True  # No feature data available — can't filter
        features = {f.lower() for f in car.features}
        has_pano = any("panorama" in f for f in features)
        has_camera = any("backkamera" in f or "360" in f for f in features)
        has_leather = any("skinn" in f for f in features)
        has_blis = any("blis" in f for f in features)
        return has_pano and has_camera and has_leather and has_blis

    def passes_hard_filters(self, car: Car) -> bool:
        """Return True if car passes all hard filter criteria."""
        if car.model not in self.criteria.ALLOWED_MODELS:
            return False
        if car.year < self.criteria.MIN_YEAR:
            return False
        if car.price_sek > self.criteria.MAX_PRICE_SEK:
            return False
        if not self.is_allowed_fuel(car):
            return False
        if not self.has_required_features(car):
            return False
        return True

    def filter(self, cars: list[Car]) -> list[Car]:
        """Apply all hard filters and return only passing cars."""
        return [c for c in cars if self.passes_hard_filters(c)]
