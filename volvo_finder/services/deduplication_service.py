"""Deduplication service for removing duplicate car listings."""
from models.car import Car


class DeduplicationService:
    """
    Removes duplicate car listings using:
    1. VIN-based deduplication (exact match)
    2. Fuzzy matching: model + year + price (±2%) + mileage (±2%)

    For ±2% tolerance, prices are rounded to nearest 6000 SEK (2% of 300k)
    and mileage is rounded to nearest 320 mil (2% of 16000).
    """

    PRICE_BUCKET = 6000     # Round to nearest 6000 SEK (≈2% of max price 300k)
    MILEAGE_BUCKET = 320    # Round to nearest 320 mil (≈2% of max diesel 16000)

    def deduplicate(self, cars: list[Car]) -> list[Car]:
        """
        Remove duplicates. For same-key duplicates, keep the first seen.
        Returns deduplicated list preserving original order.
        """
        seen: dict[str, Car] = {}
        result: list[Car] = []
        for car in cars:
            key = self._get_key(car)
            if key not in seen:
                seen[key] = car
                result.append(car)
        return result

    def _get_key(self, car: Car) -> str:
        """
        Generate deduplication key.
        - If VIN is available: use exact VIN
        - Otherwise: model:year:price_bucket:mileage_bucket
        """
        if car.vin:
            return f"vin:{car.vin.upper().strip()}"

        # Fuzzy buckets
        price_bucket = round(car.price_sek / self.PRICE_BUCKET)
        mil_bucket = round(car.mileage_mil / self.MILEAGE_BUCKET)
        return f"{car.model}:{car.year}:{price_bucket}:{mil_bucket}"
