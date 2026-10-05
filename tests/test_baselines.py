"""Tests for the time split and the two baselines."""

import pandas as pd
import pytest

from pogoh.baselines import HourOfWeekRate, hour_of_week, persistence
from pogoh.evaluate import time_split

# Monday 2026-10-05 08:00 in Pittsburgh is 12:00 UTC.
MON_8AM = pd.Timestamp("2026-10-05 12:00", tz="UTC")


def weeks_later(t, n):
    """Same local wall-clock time n weeks later. Daylight saving ends Nov 1,
    so adding 7 x 24 hours in UTC would drift by an hour."""
    return (t.tz_convert("America/New_York") + pd.DateOffset(weeks=n)).tz_convert("UTC")


def test_split_is_ordered_in_time_with_gaps():
    t = pd.date_range("2026-10-05", periods=2000, freq="5min", tz="UTC")
    df = pd.DataFrame({"t": t})
    split = time_split(df)

    train, val, test = (df.loc[split == s, "t"] for s in ("train", "validation", "test"))
    assert train.max() < val.min() < val.max() < test.min()
    assert val.min() - train.max() > pd.Timedelta(minutes=60)
    assert test.min() - val.max() > pd.Timedelta(minutes=60)
    assert split.isna().sum() > 0  # gap rows dropped
    assert abs(len(test) / len(df) - 0.15) < 0.01


def test_split_rejects_bad_fractions():
    with pytest.raises(ValueError):
        time_split(pd.DataFrame({"t": pd.date_range("2026-10-05", periods=10, tz="UTC")}), (0.5, 0.2, 0.2))


def test_persistence():
    df = pd.DataFrame({"free_bikes": [0, 3], "empty_slots": [10, 0]})
    assert persistence(df, "stockout_30").tolist() == [1.0, 0.0]
    assert persistence(df, "dock_full_30").tolist() == [0.0, 1.0]


def test_hour_of_week_uses_local_time():
    assert hour_of_week(pd.Series([MON_8AM])).tolist() == [8]


def _rows(station, t, labels):
    return pd.DataFrame({"station_id": station, "t": t, "stockout_30": pd.array(labels, dtype="Int8")})


def test_hour_of_week_rate_and_fallbacks():
    train = pd.concat([
        # Station A, Monday 8am: 6 rows, 3 stockouts -> slot rate 0.5
        _rows("A", [weeks_later(MON_8AM, i) for i in range(6)], [1, 1, 1, 0, 0, 0]),
        # Station A, Monday 9am: only 2 rows, too few -> falls back to A's overall rate
        _rows("A", [weeks_later(MON_8AM + pd.Timedelta(hours=1), i) for i in range(2)], [1, 1]),
        # Station B: always stocked
        _rows("B", [weeks_later(MON_8AM, i) for i in range(6)], [0] * 6),
        # Missing labels are ignored
        _rows("B", [MON_8AM + pd.Timedelta(hours=2)], [pd.NA]),
    ], ignore_index=True)
    model = HourOfWeekRate("stockout_30").fit(train)

    test = pd.DataFrame({
        "station_id": ["A", "A", "B", "Z"],
        "t": [MON_8AM, MON_8AM + pd.Timedelta(hours=1), MON_8AM, MON_8AM],
    })
    p = model.predict_proba(test)

    assert p.iloc[0] == pytest.approx(0.5)        # slot rate
    assert p.iloc[1] == pytest.approx(5 / 8)      # A overall: 5 stockouts in 8 rows
    assert p.iloc[2] == pytest.approx(0.0)        # B slot rate
    assert p.iloc[3] == pytest.approx(5 / 14)     # unseen station: global rate
