"""Tests for the weather table: parsing, live vs backfill, caching, features. No network calls."""

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

from pogoh.weather import (
    CACHE_COLUMNS, DRY_HOURS_CAP, FEATURE_COLUMNS, live_hourly, load_cache, merge_sources,
    parse_response, update_cache, weather_features,
)
from pogoh.weather_live import make_weather_record

FIXTURES = Path(__file__).parent / "fixtures"
# Real historical weather (ERA5) API response, 2026-10-04 08:00 to 13:00 UTC.
BACKFILL = FIXTURES / "weather_sample.json"
# Real live response fetched 2026-11-12 just after 20:00 UTC: hours 17:00 to 22:00.
LIVE = FIXTURES / "weather_live_sample.json"


@pytest.fixture
def weather():
    return parse_response(json.loads(BACKFILL.read_text(encoding="utf-8")))


@pytest.fixture
def live_payload():
    return json.loads(LIVE.read_text(encoding="utf-8"))


def hourly(start, precip, temp=None):
    """Hand-made hourly table starting at `start` (UTC)."""
    n = len(precip)
    df = pd.DataFrame({"time": pd.date_range(start, periods=n, freq="h", tz="UTC")})
    for column in CACHE_COLUMNS[1:-1]:
        df[column] = 0.0
    df["precipitation_mm"] = precip
    df["temperature_c"] = temp if temp is not None else range(n)
    df["source"] = "backfill"
    return df


# --- parsing ------------------------------------------------------------------

def test_parse_real_response(weather):
    assert len(weather) == 6
    assert list(weather.columns) == CACHE_COLUMNS
    assert str(weather["time"].dt.tz) == "UTC"
    assert weather["time"].iloc[0] == pd.Timestamp("2026-10-04 08:00", tz="UTC")
    assert weather["temperature_c"].iloc[0] == 12.4
    assert weather["apparent_temperature_c"].iloc[0] == 11.5
    assert weather["is_day"].tolist() == [0, 0, 0, 0, 1, 1]  # sunrise between 11 and 12 UTC
    assert (weather["source"] == "backfill").all()


def test_missing_variable_becomes_nan(weather):
    payload = json.loads(BACKFILL.read_text(encoding="utf-8"))
    del payload["hourly"]["snowfall"]
    assert parse_response(payload)["snowfall_cm"].isna().all()


# --- live records -------------------------------------------------------------

def test_live_keeps_only_hours_that_had_ended(live_payload):
    record = make_weather_record("2026-11-12T20:15:00+00:00", payload=live_payload)
    table = live_hourly([record])
    hours = table["time"].dt.hour.tolist()
    assert hours == [17, 18, 19, 20]  # 21:00 and 22:00 were forecasts
    assert (table["source"] == "live").all()
    assert table.loc[table["time"].dt.hour == 20, "precipitation_mm"].item() == 0.4


def test_live_uses_the_earliest_snapshot_after_each_hour(live_payload):
    first = make_weather_record("2026-11-12T20:15:00+00:00", payload=live_payload)
    later_payload = json.loads(json.dumps(live_payload))
    later_payload["hourly"]["temperature_2m"] = [99.0] * 6  # revised values an hour later
    later = make_weather_record("2026-11-12T21:15:00+00:00", payload=later_payload)
    failed = make_weather_record("2026-11-12T20:30:00+00:00", error_text="ConnectionError: down")

    table = live_hourly([later, failed, first])
    temps = dict(zip(table["time"].dt.hour, table["temperature_c"]))
    assert temps[20] == 7.3   # known at 20:15, not the 21:15 revision
    assert temps[21] == 99.0  # only the later snapshot had seen 21:00 end


def test_no_live_records_gives_empty_table():
    assert list(live_hourly([]).columns) == CACHE_COLUMNS


# --- merging and cache --------------------------------------------------------

def test_live_beats_backfill_and_fresh_backfill_beats_old():
    old = hourly("2026-10-04 08:00", [0, 0, 0], temp=[1.0, 1.0, 1.0])
    fresh = hourly("2026-10-04 08:00", [0, 0, 0], temp=[2.0, 2.0, 2.0])
    live = hourly("2026-10-04 09:00", [0], temp=[5.0]).assign(source="live")

    merged = merge_sources(old, live, fresh)  # live given before fresh on purpose
    assert merged["temperature_c"].tolist() == [2.0, 5.0, 2.0]
    assert merged["source"].tolist() == ["backfill", "live", "backfill"]


def test_update_cache_refetches_recent_days_and_keeps_newest(tmp_path, weather):
    path = tmp_path / "weather.csv"
    calls = []
    now = pd.Timestamp("2026-10-05 00:00", tz="UTC")

    def fake_fetch(start, end):
        calls.append((start, end))
        return weather

    update_cache(dt.date(2026, 10, 4), path=path, today=dt.date(2026, 10, 4), now=now, fetch=fake_fetch)

    revised = weather.copy()
    revised["temperature_c"] = 20.0
    update_cache(dt.date(2026, 10, 4), path=path, today=dt.date(2026, 10, 5), now=now,
                 fetch=lambda s, e: revised)

    cached = load_cache(path)
    assert len(cached) == 6  # same hours, not duplicated
    assert (cached["temperature_c"] == 20.0).all()  # revised values replaced old ones
    assert calls == [(dt.date(2026, 10, 4), dt.date(2026, 10, 4))]


def test_update_cache_drops_future_hours_and_keeps_live(tmp_path, weather):
    path = tmp_path / "weather.csv"
    live = hourly("2026-10-04 09:00", [0], temp=[5.0]).assign(source="live")
    now = pd.Timestamp("2026-10-04 10:30", tz="UTC")
    update_cache(dt.date(2026, 10, 4), path=path, today=dt.date(2026, 10, 4), now=now,
                 fetch=lambda s, e: weather, live=live)
    # A later update without live records must not lose the live hour.
    update_cache(dt.date(2026, 10, 4), path=path, today=dt.date(2026, 10, 4), now=now,
                 fetch=lambda s, e: weather)

    cached = load_cache(path)
    assert cached["time"].dt.hour.tolist() == [8, 9, 10]  # 11:00 onward had not happened
    assert cached["source"].tolist() == ["backfill", "live", "backfill"]
    assert cached["temperature_c"].tolist() == [12.4, 5.0, 13.7]


def test_cache_in_old_layout_is_ignored(tmp_path):
    path = tmp_path / "weather.csv"
    path.write_text("time,temperature_c,precipitation_mm\n2026-10-04 08:00:00+00:00,12.4,0.0\n")
    assert len(load_cache(path)) == 0


# --- features -----------------------------------------------------------------

def test_uses_latest_hour_at_or_before_t_never_the_next_hour(weather):
    t = pd.Series(pd.to_datetime(["2026-10-04 10:00", "2026-10-04 10:59", "2026-10-04 11:00"], utc=True))
    f = weather_features(t, weather)
    assert list(f.columns) == FEATURE_COLUMNS
    # 10:00 and 10:59 both use the 10:00 row (13.7); only 11:00 sees the 11:00 row (12.9).
    assert f["temperature_c"].tolist() == [13.7, 13.7, 12.9]


def test_missing_when_weather_is_too_old(weather):
    t = pd.Series(pd.to_datetime(["2026-10-04 16:30"], utc=True))  # newest row is 13:00
    assert weather_features(t, weather).isna().all(axis=None)


def test_precip_3h_and_hours_since_precip():
    w = hourly("2026-10-04 00:00", [0.0, 1.0, 0.0, 0.0, 0.0, 2.0])
    t = pd.Series(w["time"] + pd.Timedelta(minutes=10))
    f = weather_features(t, w)
    # precip_3h at hour h: hours ending h-2, h-1, h.
    assert f["precip_3h_mm"].tolist() == [0.0, 1.0, 1.0, 1.0, 0.0, 2.0]
    assert f["hours_since_precip"].tolist() == [DRY_HOURS_CAP, 0.0, 1.0, 2.0, 3.0, 0.0]


def test_weather_features_never_read_later_hours():
    w = hourly("2026-10-04 00:00", [0.0, 1.0, 0.0, 0.5, 0.0, 0.0, 3.0, 0.0])
    t = pd.Series(pd.date_range("2026-10-04 00:05", periods=40, freq="10min", tz="UTC"))
    cutoff = pd.Timestamp("2026-10-04 04:30", tz="UTC")
    before = weather_features(t, w)

    scrambled = w.copy()
    later = scrambled["time"] > cutoff
    scrambled.loc[later, CACHE_COLUMNS[1:-1]] = 999.0
    removed = w[~later]
    early = t <= cutoff
    pd.testing.assert_frame_equal(weather_features(t, scrambled)[early], before[early])
    pd.testing.assert_frame_equal(weather_features(t, removed)[early], before[early])
