"""Tests for FilterService hard filter logic."""
import sys
import os

# Make sure the volvo_finder package root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from models.car import Car
from models.filter_criteria import FilterCriteria
from services.filter_service import FilterService

# ---- Helper factory ----

def make_car(
    model="V60",
    year=2020,
    price_sek=250_000,
    mileage_mil=8_000.0,
    fuel="diesel",
    features=None,
    **kwargs,
) -> Car:
    if features is None:
        features = {"panoramatak", "backkamera", "skinnstolar", "blis"}
    return Car(
        source="test",
        url=f"https://example.com/{model}-{year}-{price_sek}",
        model=model,
        year=year,
        price_sek=price_sek,
        mileage_mil=mileage_mil,
        fuel=fuel,
        features=features,
        **kwargs,
    )


@pytest.fixture
def svc():
    return FilterService()


# ---- Model tests ----

def test_allowed_model_v60(svc):
    car = make_car(model="V60")
    assert svc.passes_hard_filters(car)


def test_allowed_model_v90(svc):
    car = make_car(model="V90")
    assert svc.passes_hard_filters(car)


def test_allowed_model_xc60(svc):
    car = make_car(model="XC60")
    assert svc.passes_hard_filters(car)


def test_disallowed_model_s60(svc):
    car = make_car(model="S60")
    assert not svc.passes_hard_filters(car)


def test_disallowed_model_xc90(svc):
    car = make_car(model="XC90")
    assert not svc.passes_hard_filters(car)


# ---- Year tests ----

def test_year_exactly_2018_allowed(svc):
    car = make_car(year=2018)
    assert svc.passes_hard_filters(car)


def test_year_2017_excluded(svc):
    car = make_car(year=2017)
    assert not svc.passes_hard_filters(car)


def test_year_2016_excluded(svc):
    car = make_car(year=2016)
    assert not svc.passes_hard_filters(car)


def test_year_2023_allowed(svc):
    car = make_car(year=2023)
    assert svc.passes_hard_filters(car)


# ---- Price tests ----

def test_price_exactly_300000_allowed(svc):
    car = make_car(price_sek=300_000)
    assert svc.passes_hard_filters(car)


def test_price_300001_excluded(svc):
    car = make_car(price_sek=300_001)
    assert not svc.passes_hard_filters(car)


def test_price_500000_excluded(svc):
    car = make_car(price_sek=500_000)
    assert not svc.passes_hard_filters(car)


def test_price_100000_allowed(svc):
    car = make_car(price_sek=100_000)
    assert svc.passes_hard_filters(car)


# ---- Fuel tests ----

def test_diesel_within_limit_allowed(svc):
    car = make_car(fuel="diesel", mileage_mil=16_000.0)
    assert svc.passes_hard_filters(car)


def test_diesel_over_limit_excluded(svc):
    car = make_car(fuel="diesel", mileage_mil=16_001.0)
    assert not svc.passes_hard_filters(car)


def test_recharge_within_limit_allowed(svc):
    car = make_car(fuel="recharge", mileage_mil=11_000.0)
    assert svc.passes_hard_filters(car)


def test_recharge_over_limit_excluded(svc):
    car = make_car(fuel="recharge", mileage_mil=11_001.0)
    assert not svc.passes_hard_filters(car)


def test_bensin_excluded(svc):
    car = make_car(fuel="bensin", mileage_mil=5_000.0)
    assert not svc.passes_hard_filters(car)


def test_el_excluded(svc):
    car = make_car(fuel="el", mileage_mil=5_000.0)
    assert not svc.passes_hard_filters(car)


def test_hybrid_excluded(svc):
    car = make_car(fuel="hybrid", mileage_mil=5_000.0)
    assert not svc.passes_hard_filters(car)


# ---- Feature tests ----

def test_missing_panoramatak_excluded(svc):
    car = make_car(features={"backkamera", "skinnstolar", "blis"})
    assert not svc.passes_hard_filters(car)


def test_missing_backkamera_and_360_excluded(svc):
    """Must have at least backkamera OR 360kamera."""
    car = make_car(features={"panoramatak", "skinnstolar", "blis"})
    assert not svc.passes_hard_filters(car)


def test_backkamera_without_360_passes(svc):
    """backkamera alone satisfies camera requirement."""
    car = make_car(features={"panoramatak", "backkamera", "skinnstolar", "blis"})
    assert svc.passes_hard_filters(car)


def test_360kamera_without_backkamera_passes(svc):
    """360kamera alone satisfies camera requirement."""
    car = make_car(features={"panoramatak", "360kamera", "skinnstolar", "blis"})
    assert svc.passes_hard_filters(car)


def test_missing_skinnstolar_excluded(svc):
    car = make_car(features={"panoramatak", "backkamera", "blis"})
    assert not svc.passes_hard_filters(car)


def test_missing_blis_excluded(svc):
    car = make_car(features={"panoramatak", "backkamera", "skinnstolar"})
    assert not svc.passes_hard_filters(car)


def test_all_required_features_passes(svc):
    car = make_car(features={"panoramatak", "backkamera", "skinnstolar", "blis"})
    assert svc.passes_hard_filters(car)


def test_all_required_plus_extras_passes(svc):
    car = make_car(features={"panoramatak", "backkamera", "skinnstolar", "blis", "bowers_wilkins", "360kamera"})
    assert svc.passes_hard_filters(car)


# ---- filter() method ----

def test_filter_returns_only_passing_cars(svc):
    passing = make_car(model="V60", year=2020, price_sek=200_000, fuel="diesel", mileage_mil=5_000)
    failing_year = make_car(model="V60", year=2015, price_sek=200_000, fuel="diesel", mileage_mil=5_000)
    failing_fuel = make_car(model="V60", year=2020, price_sek=200_000, fuel="bensin", mileage_mil=5_000)

    result = svc.filter([passing, failing_year, failing_fuel])
    assert result == [passing]


def test_filter_empty_input(svc):
    assert svc.filter([]) == []


def test_filter_all_pass(svc):
    # make_car generates unique urls from model+year+price, so vary prices slightly
    cars = [
        make_car(price_sek=200_000 + i * 1000)
        for i in range(5)
    ]
    assert len(svc.filter(cars)) == 5
