"""Future-state targets: will a station be empty (or full) H minutes from now?

For each observation of a station at time t:

- stockout_30, stockout_60: 1 if free_bikes is 0 at t + 30 or t + 60 minutes
- dock_full_30: 1 if empty_slots is 0 at t + 30 minutes

Polls are irregular, so "at t + H" means the observation of the same station
nearest to t + H, and only if it is within MATCH_TOLERANCE (3 minutes). If no
observation is that close, the label is missing (NA) rather than guessed.
Each target is missing independently: a row can have a 30 minute label but
no 60 minute one. Training for a target drops rows where it is missing.

Each label also gets a transition flag: 1 when the current state differs
from the future state (for example bikes now, empty later). These are the
rows where simply predicting "no change" fails.
"""

import pandas as pd

MATCH_TOLERANCE = pd.Timedelta(minutes=3)

# target name -> (horizon in minutes, column checked for zero)
TARGETS = {
    "stockout_30": (30, "free_bikes"),
    "stockout_60": (60, "free_bikes"),
    "dock_full_30": (30, "empty_slots"),
}


def _future_values(obs, horizon_minutes, column):
    """Value of `column` at the observation nearest t + H, per row of obs.

    Returns (values, matched_times) aligned to obs.index, NA where unmatched.
    """
    left = pd.DataFrame({
        "row": obs.index,
        "station_id": obs["station_id"],
        "t_target": obs["t"] + pd.Timedelta(minutes=horizon_minutes),
    }).sort_values("t_target")
    right = pd.DataFrame({
        "station_id": obs["station_id"],
        "t_future": obs["t"],
        "future_value": obs[column],
    }).sort_values("t_future")

    matched = pd.merge_asof(
        left, right,
        left_on="t_target", right_on="t_future",
        by="station_id", direction="nearest", tolerance=MATCH_TOLERANCE,
    ).set_index("row").reindex(obs.index)
    return matched["future_value"], matched["t_future"]


def add_labels(obs):
    """Return a copy of obs with label, transition, and match-time columns.

    Added columns, per target name T:
      T                1, 0, or NA
      T_transition     1 if the current state differs from T, NA if T is NA
      T_matched_t      time of the observation used for T (for checking)
    """
    out = obs.copy()
    for name, (horizon, column) in TARGETS.items():
        future, matched_t = _future_values(obs, horizon, column)
        label = (future == 0).astype("Int8").where(future.notna())
        now = (obs[column] == 0).astype("Int8")
        out[name] = label
        out[f"{name}_transition"] = (now != label).astype("Int8").where(label.notna())
        out[f"{name}_matched_t"] = matched_t
    return out
