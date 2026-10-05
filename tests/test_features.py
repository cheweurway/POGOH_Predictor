"""Tests for features, especially that no feature reads a later row.

The leakage tests build a realistic synthetic history: several stations,
irregular poll times, and random gaps. They then rebuild features after
removing or scrambling every row later than a cutoff. If any feature for a
row at or before the cutoff changes, that feature was reading the future.
"""

import numpy as np
import pandas as pd
import pytest

from pogoh.calendar import load_calendar
from pogoh.features import build_features

T0 = pd.Timestamp("2026-10-09 14:00", tz="UTC")  # 10:00 in Pittsburgh, a Friday


def synthetic_obs(seed=0, n_polls=60, stations=("A", "B", "C")):
    rng = np.random.default_rng(seed)
    # Irregular gaps between polls: mostly 5 minutes, sometimes longer.
    gaps = rng.choice([4.5, 5, 5, 5.5, 10, 25], size=n_polls, p=[0.15, 0.4, 0.2, 0.1, 0.1, 0.05])
    times = T0 + pd.to_timedelta(np.cumsum(gaps), unit="min")
    rows = []
    for station in stations:
        bikes = int(rng.integers(0, 15))
        for t in times:
            if rng.random() < 0.1:
                continue  # this station missing from this poll
            bikes = int(np.clip(bikes + rng.integers(-3, 4), 0, 15))
            rows.append({"station_id": station, "t": t, "free_bikes": bikes,
                         "empty_slots": 15 - bikes, "ebikes": bikes // 3})
    # Shuffle so nothing relies on input order.
    return pd.DataFrame(rows).sample(frac=1, random_state=seed).reset_index(drop=True)


@pytest.fixture(scope="module")
def calendar():
    return load_calendar()


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.parametrize("cut_fraction", [0.25, 0.5, 0.9])
def test_removing_future_rows_changes_nothing(seed, cut_fraction, calendar):
    obs = synthetic_obs(seed)
    cutoff = obs["t"].quantile(cut_fraction)
    full = build_features(obs, calendar)

    past_only = obs[obs["t"] <= cutoff]
    truncated = build_features(past_only, calendar)

    pd.testing.assert_frame_equal(full.loc[past_only.index], truncated)


@pytest.mark.parametrize("seed", [0, 1])
def test_scrambling_future_rows_changes_nothing(seed, calendar):
    obs = synthetic_obs(seed)
    cutoff = obs["t"].median()
    future = obs["t"] > cutoff

    poisoned = obs.copy()
    poisoned.loc[future, ["free_bikes", "empty_slots", "ebikes"]] = 999

    before = build_features(obs, calendar)
    after = build_features(poisoned, calendar)
    pd.testing.assert_frame_equal(before.loc[~future], after.loc[~future])


def _two_station_obs(rows):
    df = pd.DataFrame(rows, columns=["station_id", "minute", "free_bikes", "empty_slots"])
    df["t"] = T0 + pd.to_timedelta(df.pop("minute"), unit="min")
    df["ebikes"] = 0
    return df


def test_lag_uses_latest_row_at_or_before_t_minus_lag():
    obs = _two_station_obs([("A", 0, 8, 7), ("A", 5, 6, 9), ("A", 15, 3, 12)])
    f = build_features(obs)
    row = f.iloc[2]  # t = 15, so t - 10 = 5
    assert row["free_bikes_lag10"] == 6
    assert row["free_bikes_chg10"] == 3 - 6


def test_lag_is_missing_when_history_is_too_old():
    obs = _two_station_obs([("A", 0, 8, 7), ("A", 20, 3, 12)])  # t - 10 = 10, newest is 10 min older
    f = build_features(obs)
    assert np.isnan(f.iloc[1]["free_bikes_lag10"])


def test_lag_never_crosses_stations():
    obs = _two_station_obs([("B", 0, 8, 7), ("A", 10, 3, 12)])
    f = build_features(obs)
    assert np.isnan(f.loc[1, "free_bikes_lag10"])


def test_current_state_and_time_features(calendar):
    obs = _two_station_obs([("A", 0, 0, 15), ("A", 5, 15, 0)])
    f = build_features(obs, calendar)
    assert f["capacity"].tolist() == [15, 15]
    assert f["is_empty"].tolist() == [1, 0] and f["is_full"].tolist() == [0, 1]
    assert f["frac_full"].tolist() == [0.0, 1.0]
    assert f["minutes_since_prev"].isna().iloc[0] and f["minutes_since_prev"].iloc[1] == 5
    # T0 is Friday 10:00 local, during Pitt fall break but a CMU class day.
    assert f["day_of_week"].iloc[0] == 4 and f["is_weekend"].iloc[0] == 0
    assert f["hour_cos"].iloc[0] == pytest.approx(np.cos(2 * np.pi * 10 / 24))
    assert f["pitt_no_classes"].iloc[0] == 1 and f["cmu_no_classes"].iloc[0] == 0
