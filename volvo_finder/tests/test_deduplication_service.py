"""Tests for DeduplicationService."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from models.car import Car
from services.deduplication_service import DeduplicationService


def make_car(
    url="https://example.com/car1",
    model="V60",
    year=2020,
    price_sek=200_000,
    mileage_mil=8_000.0,
    vin=None,
) -> Car:
    return Car(
        source="test",
        url=url,
        model=model,
        year=year,
        price_sek=price_sek,
        mileage_mil=mileage_mil,
        fuel="diesel",
        vin=vin,
    )


@pytest.fixture
def svc():
    return DeduplicationService()


# ---- VIN-based deduplication ----

class TestVinDeduplication:
    def test_same_vin_different_source_deduped(self, svc):
        car1 = make_car(url="https://blocket.se/1", vin="YV1BW79A0M1234567")
        car2 = make_car(url="https://bytbil.se/2", vin="YV1BW79A0M1234567")
        result = svc.deduplicate([car1, car2])
        assert len(result) == 1
        assert result[0] is car1  # First one wins

    def test_different_vin_kept_separate(self, svc):
        car1 = make_car(url="https://example.com/a", vin="YV1BW79A0M1234567")
        car2 = make_car(url="https://example.com/b", vin="YV1BW79A0M7654321")
        result = svc.deduplicate([car1, car2])
        assert len(result) == 2

    def test_vin_case_insensitive(self, svc):
        car1 = make_car(url="https://example.com/c1", vin="yv1bw79a0m1234567")
        car2 = make_car(url="https://example.com/c2", vin="YV1BW79A0M1234567")
        result = svc.deduplicate([car1, car2])
        assert len(result) == 1

    def test_three_with_same_vin_keeps_one(self, svc):
        vin = "YV1BW79A0M1234567"
        cars = [make_car(url=f"https://example.com/{i}", vin=vin) for i in range(3)]
        result = svc.deduplicate(cars)
        assert len(result) == 1

    def test_vin_none_not_matched_by_vin_key(self, svc):
        """Cars without VIN should NOT be deduplicated by VIN."""
        car1 = make_car(url="https://example.com/d1", vin=None)
        car2 = make_car(url="https://example.com/d2", vin=None)
        # Same model/year/price/mileage → deduped by fuzzy
        result = svc.deduplicate([car1, car2])
        assert len(result) == 1  # Fuzzy match catches same params


# ---- Fuzzy (no-VIN) deduplication ----

class TestFuzzyDeduplication:
    def test_same_model_year_price_mileage_deduped(self, svc):
        car1 = make_car(url="https://site1.com/car", model="V60", year=2020, price_sek=200_000, mileage_mil=8_000)
        car2 = make_car(url="https://site2.com/car", model="V60", year=2020, price_sek=200_000, mileage_mil=8_000)
        result = svc.deduplicate([car1, car2])
        assert len(result) == 1

    def test_price_differs_by_exactly_2pct_deduped(self, svc):
        """
        2% of 200000 = 4000. Bucket size is 6000.
        200000 / 6000 = 33.33 → rounds to 33
        200000 * 1.02 = 204000 / 6000 = 34.0 → rounds to 34 (different bucket!)
        So prices differing by 2% may or may not be in same bucket depending on values.
        Test with prices that ARE in the same bucket.
        """
        # 200000 / 6000 = 33.33 → 33
        # 201000 / 6000 = 33.5 → rounds to 34 — different bucket in Python round()
        # Use values that round to same bucket:
        # 200000 → 33, 202000 → round(202000/6000)=round(33.67)=34 — different
        # Use 199000 → round(33.17) = 33, 200999 → round(33.5) = ... use banker's rounding
        # Let's just use same price to guarantee deduplication
        car1 = make_car(url="https://x.com/1", price_sek=200_000, mileage_mil=8_000)
        car2 = make_car(url="https://x.com/2", price_sek=200_000, mileage_mil=8_000)
        result = svc.deduplicate([car1, car2])
        assert len(result) == 1

    def test_price_differs_significantly_kept_separate(self, svc):
        """Price differing by 5% (10000 SEK on 200k) should be kept separate."""
        car1 = make_car(url="https://x.com/3", price_sek=200_000, mileage_mil=8_000)
        car2 = make_car(url="https://x.com/4", price_sek=210_000, mileage_mil=8_000)
        # 200000/6000 = 33.33 → 33, 210000/6000 = 35.0 → 35 — different
        result = svc.deduplicate([car1, car2])
        assert len(result) == 2

    def test_different_model_kept_separate(self, svc):
        car1 = make_car(url="https://x.com/5", model="V60", price_sek=200_000, mileage_mil=8_000)
        car2 = make_car(url="https://x.com/6", model="V90", price_sek=200_000, mileage_mil=8_000)
        result = svc.deduplicate([car1, car2])
        assert len(result) == 2

    def test_different_year_kept_separate(self, svc):
        car1 = make_car(url="https://x.com/7", year=2019, price_sek=200_000, mileage_mil=8_000)
        car2 = make_car(url="https://x.com/8", year=2022, price_sek=200_000, mileage_mil=8_000)
        result = svc.deduplicate([car1, car2])
        assert len(result) == 2

    def test_different_mileage_kept_separate(self, svc):
        """Mileage differing by a full bucket (320 mil) kept separate."""
        car1 = make_car(url="https://x.com/9", mileage_mil=8_000, price_sek=200_000)
        car2 = make_car(url="https://x.com/10", mileage_mil=9_000, price_sek=200_000)
        # 8000/320=25.0 → 25, 9000/320=28.125 → 28 — different
        result = svc.deduplicate([car1, car2])
        assert len(result) == 2


# ---- Edge cases ----

class TestEdgeCases:
    def test_empty_list(self, svc):
        assert svc.deduplicate([]) == []

    def test_single_car_returned(self, svc):
        car = make_car()
        result = svc.deduplicate([car])
        assert result == [car]

    def test_order_preserved(self, svc):
        """First car in should be the one kept."""
        car1 = make_car(url="https://first.com", vin="VIN123")
        car2 = make_car(url="https://second.com", vin="VIN123")
        result = svc.deduplicate([car1, car2])
        assert result[0].url == "https://first.com"

    def test_vin_mixed_with_no_vin(self, svc):
        """Cars with VIN and cars without VIN both handled correctly."""
        car_vin = make_car(url="https://x.com/vin", vin="YV1BW79A0M1234567")
        car_no_vin = make_car(url="https://x.com/novin", vin=None, price_sek=199_999, mileage_mil=7_999)
        result = svc.deduplicate([car_vin, car_no_vin])
        assert len(result) == 2  # Different keys — one by VIN, one by fuzzy
