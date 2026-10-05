"""Model inputs built only from data at or before each row's time t.

build_features(obs) returns one row per observation (same index as obs) with:

Current state
  free_bikes, empty_slots, ebikes, capacity (free plus empty), frac_full,
  is_empty, is_full

Recent history, for L in 10, 30, 60 minutes
  free_bikes_lagL, empty_slots_lagL: values from the latest observation of
    the same station at or before t - L, if it is at most LAG_TOLERANCE older
    than t - L. Otherwise missing (NaN); gaps are never filled in.
  free_bikes_chgL: free_bikes now minus free_bikes_lagL
  minutes_since_prev: minutes since this station's previous observation,
    which shows how fresh the history is and flags data gaps

Time, in America/New_York local time
  hour_sin, hour_cos (hour of day on a circle, so 23:59 is next to 00:00),
  day_of_week (0 = Monday), is_weekend

Academic calendar (if a calendar is given)
  cmu_in_term, cmu_no_classes, cmu_finals, pitt_in_term, pitt_no_classes,
  pitt_finals

Leakage rule: no feature for time t may read any row after t. The tests in
tests/test_features.py enforce this by rebuilding features with all later
rows removed or scrambled and checking that nothing changes.
"""

import numpy as np
import pandas as pd

from pogoh.calendar import calendar_flags

LOCAL_TZ = "America/New_York"
LAGS_MINUTES = (10, 30, 60)
LAG_TOLERANCE = pd.Timedelta(minutes=5)


def _lagged(obs, lag_minutes, columns):
    """Values of `columns` from the latest observation at or before t - lag."""
    left = pd.DataFrame({
        "row": obs.index,
        "station_id": obs["station_id"],
        "t_lag": obs["t"] - pd.Timedelta(minutes=lag_minutes),
    }).sort_values("t_lag")
    right = obs[["station_id", "t", *columns]].sort_values("t")

    matched = pd.merge_asof(
        left, right,
        left_on="t_lag", right_on="t",
        by="station_id", direction="backward", tolerance=LAG_TOLERANCE,
    ).set_index("row").reindex(obs.index)
    return matched[columns]


def build_features(obs, calendar=None):
    f = pd.DataFrame(index=obs.index)

    # Current state
    f["free_bikes"] = obs["free_bikes"]
    f["empty_slots"] = obs["empty_slots"]
    f["ebikes"] = obs["ebikes"]
    f["capacity"] = obs["free_bikes"] + obs["empty_slots"]
    f["frac_full"] = (obs["free_bikes"] / f["capacity"].replace(0, np.nan)).astype(float)
    f["is_empty"] = (obs["free_bikes"] == 0).astype("int8")
    f["is_full"] = (obs["empty_slots"] == 0).astype("int8")

    # Recent history
    for lag in LAGS_MINUTES:
        past = _lagged(obs, lag, ["free_bikes", "empty_slots"])
        f[f"free_bikes_lag{lag}"] = past["free_bikes"].astype(float)
        f[f"empty_slots_lag{lag}"] = past["empty_slots"].astype(float)
        f[f"free_bikes_chg{lag}"] = f["free_bikes"] - f[f"free_bikes_lag{lag}"]

    ordered = obs.sort_values(["station_id", "t"])
    prev_t = ordered.groupby("station_id")["t"].shift(1)  # strictly earlier row
    f["minutes_since_prev"] = ((ordered["t"] - prev_t).dt.total_seconds() / 60).reindex(obs.index)

    # Time of day and week, in Pittsburgh local time
    local = obs["t"].dt.tz_convert(LOCAL_TZ)
    hour = local.dt.hour + local.dt.minute / 60
    f["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    f["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    f["day_of_week"] = local.dt.dayofweek.astype("int8")
    f["is_weekend"] = (f["day_of_week"] >= 5).astype("int8")

    if calendar is not None:
        f = f.join(calendar_flags(local.dt.date, calendar))

    return f
