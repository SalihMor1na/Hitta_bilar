"""Ranking service for scoring and sorting car listings."""
import statistics

from models.car import Car


class RankingService:
    """
    Scores cars based on:
    - Audio system quality (+15 for B&W, +10 for Harman Kardon)
    - Mileage below median (+5)
    - Year above median (+5)
    - Price below median (+3)
    """

    def score(self, car: Car, all_cars: list[Car]) -> float:
        """Compute score for a single car relative to the full set."""
        points = 0.0

        # Audio system bonus — check audio_system field
        if car.audio_system:
            audio = car.audio_system.lower()
            if "bowers" in audio or "b&w" in audio:
                points += 15
            elif "harman" in audio:
                points += 10

        # Also check features set (in case audio_system is not set but feature was detected)
        if points == 0:
            for f in car.features:
                fl = f.lower()
                if "bowers" in fl or "b&w" in fl:
                    points += 15
                    break
                elif "harman" in fl:
                    points += 10
                    break

        if len(all_cars) < 2:
            return points

        mileages = [c.mileage_mil for c in all_cars]
        years = [c.year for c in all_cars]
        prices = [c.price_sek for c in all_cars]

        median_mil = statistics.median(mileages)
        median_year = statistics.median(years)
        median_price = statistics.median(prices)

        if car.mileage_mil < median_mil:
            points += 5
        if car.year > median_year:
            points += 5
        if car.price_sek < median_price:
            points += 3

        return points

    def rank(self, cars: list[Car]) -> list[Car]:
        """Score all cars and return sorted by score descending."""
        for car in cars:
            car.score = self.score(car, cars)
        return sorted(cars, key=lambda c: c.score, reverse=True)
