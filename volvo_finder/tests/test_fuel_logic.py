"""Tests specifically for fuel type detection and mileage limits."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from models.car import Car
from services.filter_service import FilterService


def make_car(fuel: str, mileage_mil: float, model="V60", year=2020, price_sek=200_000) -> Car:
    return Car(
        source="test",
        url=f"https://example.com/fuel-{fuel}-{mileage_mil}",
        model=model,
        year=year,
        price_sek=price_sek,
        mileage_mil=mileage_mil,
        fuel=fuel,
        features={"panoramatak", "backkamera", "skinnstolar", "blis"},
    )


@pytest.fixture
def svc():
    return FilterService()


# ---- Fuel type detection ----

class TestFuelTypeDetection:
    """Test that is_allowed_fuel correctly identifies fuel types."""

    def test_diesel_lowercase_detected(self, svc):
        car = make_car(fuel="diesel", mileage_mil=10_000)
        assert svc.is_allowed_fuel(car) is True

    def test_diesel_uppercase_detected(self, svc):
        car = make_car(fuel="Diesel", mileage_mil=10_000)
        assert svc.is_allowed_fuel(car) is True

    def test_diesel_mixed_case_detected(self, svc):
        car = make_car(fuel="DIESEL", mileage_mil=10_000)
        assert svc.is_allowed_fuel(car) is True

    def test_recharge_t8_detected(self, svc):
        car = make_car(fuel="Recharge T8", mileage_mil=5_000)
        assert svc.is_allowed_fuel(car) is True

    def test_recharge_lowercase_detected(self, svc):
        car = make_car(fuel="recharge", mileage_mil=5_000)
        assert svc.is_allowed_fuel(car) is True

    def test_phev_detected_as_recharge(self, svc):
        car = make_car(fuel="PHEV", mileage_mil=5_000)
        assert svc.is_allowed_fuel(car) is True

    def test_phev_lowercase_detected(self, svc):
        car = make_car(fuel="phev", mileage_mil=5_000)
        assert svc.is_allowed_fuel(car) is True

    def test_laddhybrid_detected_as_recharge(self, svc):
        car = make_car(fuel="laddhybrid", mileage_mil=5_000)
        assert svc.is_allowed_fuel(car) is True

    def test_laddhybrid_uppercase_detected(self, svc):
        car = make_car(fuel="Laddhybrid", mileage_mil=5_000)
        assert svc.is_allowed_fuel(car) is True

    def test_bensin_excluded(self, svc):
        car = make_car(fuel="Bensin", mileage_mil=5_000)
        assert svc.is_allowed_fuel(car) is False

    def test_bensin_lowercase_excluded(self, svc):
        car = make_car(fuel="bensin", mileage_mil=5_000)
        assert svc.is_allowed_fuel(car) is False

    def test_el_excluded(self, svc):
        car = make_car(fuel="El", mileage_mil=5_000)
        assert svc.is_allowed_fuel(car) is False

    def test_el_lowercase_excluded(self, svc):
        car = make_car(fuel="el", mileage_mil=5_000)
        assert svc.is_allowed_fuel(car) is False

    def test_hybrid_excluded(self, svc):
        """Regular hybrid (non-plugin) is excluded."""
        car = make_car(fuel="hybrid", mileage_mil=5_000)
        assert svc.is_allowed_fuel(car) is False

    def test_okand_excluded(self, svc):
        car = make_car(fuel="okänd", mileage_mil=5_000)
        assert svc.is_allowed_fuel(car) is False


# ---- Diesel mileage boundary tests ----

class TestDieselMileageBoundary:
    """Test exact boundary conditions for diesel mileage."""

    def test_diesel_exactly_16000_mil_allowed(self, svc):
        car = make_car(fuel="diesel", mileage_mil=16_000.0)
        assert svc.is_allowed_fuel(car) is True

    def test_diesel_15999_mil_allowed(self, svc):
        car = make_car(fuel="diesel", mileage_mil=15_999.0)
        assert svc.is_allowed_fuel(car) is True

    def test_diesel_16001_mil_excluded(self, svc):
        car = make_car(fuel="diesel", mileage_mil=16_001.0)
        assert svc.is_allowed_fuel(car) is False

    def test_diesel_0_mil_allowed(self, svc):
        car = make_car(fuel="diesel", mileage_mil=0.0)
        assert svc.is_allowed_fuel(car) is True

    def test_diesel_very_high_mileage_excluded(self, svc):
        car = make_car(fuel="diesel", mileage_mil=30_000.0)
        assert svc.is_allowed_fuel(car) is False


# ---- Recharge mileage boundary tests ----

class TestRechargeMileageBoundary:
    """Test exact boundary conditions for recharge/PHEV mileage."""

    def test_recharge_exactly_11000_mil_allowed(self, svc):
        car = make_car(fuel="recharge", mileage_mil=11_000.0)
        assert svc.is_allowed_fuel(car) is True

    def test_recharge_10999_mil_allowed(self, svc):
        car = make_car(fuel="recharge", mileage_mil=10_999.0)
        assert svc.is_allowed_fuel(car) is True

    def test_recharge_11001_mil_excluded(self, svc):
        car = make_car(fuel="recharge", mileage_mil=11_001.0)
        assert svc.is_allowed_fuel(car) is False

    def test_phev_exactly_11000_mil_allowed(self, svc):
        car = make_car(fuel="phev", mileage_mil=11_000.0)
        assert svc.is_allowed_fuel(car) is True

    def test_phev_11001_mil_excluded(self, svc):
        car = make_car(fuel="phev", mileage_mil=11_001.0)
        assert svc.is_allowed_fuel(car) is False

    def test_laddhybrid_exactly_11000_mil_allowed(self, svc):
        car = make_car(fuel="laddhybrid", mileage_mil=11_000.0)
        assert svc.is_allowed_fuel(car) is True

    def test_laddhybrid_11001_mil_excluded(self, svc):
        car = make_car(fuel="laddhybrid", mileage_mil=11_001.0)
        assert svc.is_allowed_fuel(car) is False


# ---- Full passes_hard_filters integration ----

class TestFuelIntegration:
    """Integration tests that fuel rules work within full filter chain."""

    def test_diesel_16000_mil_passes_all_filters(self, svc):
        car = make_car(fuel="diesel", mileage_mil=16_000.0)
        assert svc.passes_hard_filters(car) is True

    def test_diesel_16001_fails_all_filters(self, svc):
        car = make_car(fuel="diesel", mileage_mil=16_001.0)
        assert svc.passes_hard_filters(car) is False

    def test_recharge_11000_passes_all_filters(self, svc):
        car = make_car(fuel="recharge", mileage_mil=11_000.0)
        assert svc.passes_hard_filters(car) is True

    def test_recharge_11001_fails_all_filters(self, svc):
        car = make_car(fuel="recharge", mileage_mil=11_001.0)
        assert svc.passes_hard_filters(car) is False

    def test_bensin_fails_regardless_of_mileage(self, svc):
        car = make_car(fuel="bensin", mileage_mil=100.0)
        assert svc.passes_hard_filters(car) is False
