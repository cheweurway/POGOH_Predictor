"""Tests for weather parsing, caching, and timing. No network calls."""

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

from pogoh.weather import load_cache, parse_response, update_cache, weather_features

FIXTURE = Path(__file__).parent / "fixtures" / "weather_sample.json"  # 2026-10-04 08:00 to 13:00 UTC


@pytest.fixture
def weather():
    return parse_response(json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_parse_real_response(weather):
    assert len(weather) == 6
    assert str(weather["time"].dt.tz) == "UTC"
    assert weather["time"].iloc[0] == pd.Timestamp("2026-10-04 08:00", tz="UTC")
    assert weather["temperature_c"].iloc[0] == 12.4


def test_uses_latest_hour_at_or_before_t_never_the_next_hour(weather):
    t = pd.Series(pd.to_datetime(["2026-10-04 10:00", "2026-10-04 10:59", "2026-10-04 11:00"], utc=True))
    f = weather_features(t, weather)
    # 10:00 and 10:59 both use the 10:00 row (13.7); only 11:00 sees the 11:00 row (12.9).
    assert f["temperature_c"].tolist() == [13.7, 13.7, 12.9]


def test_missing_when_weather_is_too_old(weather):
    t = pd.Series(pd.to_datetime(["2026-10-04 16:30"], utc=True))  # newest row is 13:00
    assert weather_features(t, weather).isna().all(axis=None)


def test_update_cache_refetches_recent_days_and_keeps_newest(tmp_path, weather):
    path = tmp_path / "weather.csv"
    calls = []

    def fake_fetch(start, end):
        calls.append((start, end))
        return weather

    update_cache(dt.date(2026, 10, 4), path=path, today=dt.date(2026, 10, 4), fetch=fake_fetch)

    revised = weather.copy()
    revised["temperature_c"] = 20.0
    update_cache(dt.date(2026, 10, 4), path=path, today=dt.date(2026, 10, 5), fetch=lambda s, e: revised)

    cached = load_cache(path)
    assert len(cached) == 6  # same hours, not duplicated
    assert (cached["temperature_c"] == 20.0).all()  # revised values replaced old ones
    assert calls == [(dt.date(2026, 10, 4), dt.date(2026, 10, 4))]
