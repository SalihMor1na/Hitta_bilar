"""Tests for RankingService scoring logic."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from models.car import Car
from services.ranking_service import RankingService


def make_car(
    model="V60",
    year=2020,
    price_sek=200_000,
    mileage_mil=8_000.0,
    features=None,
    audio_system=None,
    url=None,
) -> Car:
    if features is None:
        features = set()
    return Car(
        source="test",
        url=url or f"https://example.com/{model}-{year}-{price_sek}-{mileage_mil}",
        model=model,
        year=year,
        price_sek=price_sek,
        mileage_mil=mileage_mil,
        fuel="diesel",
        features=features,
        audio_system=audio_system,
    )


@pytest.fixture
def svc():
    return RankingService()


# ---- Audio system scoring ----

class TestAudioScoring:
    def test_bowers_wilkins_audio_system_gives_15(self, svc):
        car = make_car(audio_system="Bowers & Wilkins")
        # Single car — no relative scoring
        score = svc.score(car, [car])
        assert score == 15.0

    def test_harman_kardon_audio_system_gives_10(self, svc):
        car = make_car(audio_system="Harman Kardon")
        score = svc.score(car, [car])
        assert score == 10.0

    def test_bowers_in_features_gives_15(self, svc):
        car = make_car(features={"bowers_wilkins"})
        score = svc.score(car, [car])
        assert score == 15.0

    def test_harman_in_features_gives_10(self, svc):
        car = make_car(features={"harman_kardon"})
        score = svc.score(car, [car])
        assert score == 10.0

    def test_no_audio_gives_0(self, svc):
        car = make_car()
        score = svc.score(car, [car])
        assert score == 0.0

    def test_bowers_audio_system_takes_priority_over_features(self, svc):
        """When audio_system is set to B&W, should score 15 regardless of features."""
        car = make_car(audio_system="Bowers & Wilkins", features={"harman_kardon"})
        score = svc.score(car, [car])
        # audio_system check runs first and returns 15
        assert score == 15.0

    def test_bowers_case_insensitive(self, svc):
        car = make_car(audio_system="bowers & wilkins")
        score = svc.score(car, [car])
        assert score == 15.0

    def test_harman_case_insensitive(self, svc):
        car = make_car(audio_system="HARMAN KARDON")
        score = svc.score(car, [car])
        assert score == 10.0


# ---- Relative scoring (mileage, year, price) ----

class TestRelativeScoring:
    def test_lower_mileage_than_median_gives_5(self, svc):
        """Car with mileage below median gets +5."""
        low_mil = make_car(mileage_mil=3_000, url="https://example.com/low")
        high_mil = make_car(mileage_mil=13_000, url="https://example.com/high")
        all_cars = [low_mil, high_mil]
        score = svc.score(low_mil, all_cars)
        # median = 8000, low_mil < median → +5
        assert score >= 5.0

    def test_higher_mileage_than_median_no_bonus(self, svc):
        low_mil = make_car(mileage_mil=3_000, url="https://example.com/low2")
        high_mil = make_car(mileage_mil=13_000, url="https://example.com/high2")
        all_cars = [low_mil, high_mil]
        score = svc.score(high_mil, all_cars)
        # high_mil >= median → no +5
        assert score < 5.0

    def test_higher_year_than_median_gives_5(self, svc):
        old_car = make_car(year=2018, url="https://example.com/old")
        new_car = make_car(year=2023, url="https://example.com/new")
        all_cars = [old_car, new_car]
        score = svc.score(new_car, all_cars)
        # median year = 2020.5, new_car > median → +5
        assert score >= 5.0

    def test_lower_year_no_year_bonus(self, svc):
        old_car = make_car(year=2018, url="https://example.com/old2")
        new_car = make_car(year=2023, url="https://example.com/new2")
        all_cars = [old_car, new_car]
        score = svc.score(old_car, all_cars)
        assert score < 5.0

    def test_lower_price_than_median_gives_3(self, svc):
        cheap = make_car(price_sek=150_000, url="https://example.com/cheap")
        expensive = make_car(price_sek=280_000, url="https://example.com/expensive")
        all_cars = [cheap, expensive]
        score = svc.score(cheap, all_cars)
        # cheap < median → +3
        assert score >= 3.0

    def test_higher_price_no_price_bonus(self, svc):
        cheap = make_car(price_sek=150_000, url="https://example.com/cheap2")
        expensive = make_car(price_sek=280_000, url="https://example.com/expensive2")
        all_cars = [cheap, expensive]
        score = svc.score(expensive, all_cars)
        assert score < 3.0


# ---- Combined scoring ----

class TestCombinedScoring:
    def test_perfect_car_score(self, svc):
        """B&W audio + low mileage + new year + low price should score 15+5+5+3=28."""
        # Create a reference set
        perfect = make_car(
            audio_system="Bowers & Wilkins",
            mileage_mil=1_000,
            year=2023,
            price_sek=100_000,
            url="https://example.com/perfect",
        )
        avg = make_car(
            mileage_mil=10_000,
            year=2019,
            price_sek=250_000,
            url="https://example.com/avg",
        )
        all_cars = [perfect, avg]
        score = svc.score(perfect, all_cars)
        assert score == 28.0  # 15 + 5 + 5 + 3

    def test_harman_plus_relative_bonuses(self, svc):
        """Harman Kardon + low mileage + high year + low price = 10+5+5+3=23."""
        good = make_car(
            audio_system="Harman Kardon",
            mileage_mil=1_000,
            year=2023,
            price_sek=100_000,
            url="https://example.com/good",
        )
        avg = make_car(
            mileage_mil=10_000,
            year=2019,
            price_sek=250_000,
            url="https://example.com/avg2",
        )
        score = svc.score(good, [good, avg])
        assert score == 23.0  # 10 + 5 + 5 + 3

    def test_single_car_no_relative_bonus(self, svc):
        """With only one car, no relative bonuses are applied."""
        car = make_car()
        assert svc.score(car, [car]) == 0.0

    def test_rank_returns_sorted_descending(self, svc):
        low = make_car(mileage_mil=15_000, year=2018, price_sek=290_000, url="https://example.com/rank-low")
        high = make_car(
            audio_system="Bowers & Wilkins",
            mileage_mil=1_000, year=2023, price_sek=100_000,
            url="https://example.com/rank-high",
        )
        ranked = svc.rank([low, high])
        assert ranked[0].url == high.url
        assert ranked[0].score > ranked[1].score

    def test_rank_sets_score_on_car(self, svc):
        car = make_car()
        svc.rank([car])
        assert car.score == 0.0  # single car, no relative bonuses
