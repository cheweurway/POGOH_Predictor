"""Two simple baselines that any model must beat.

Baseline A, persistence: the station's state now is its state later. For a
stockout target it predicts 1 exactly when free_bikes is 0 now. It needs no
training. It is very strong for stations that sit at zero for hours, which
is why results are also reported on transition rows.

Baseline B, hour of week: the station's training-set rate of the target for
the same hour of the week (Monday 8:00 to 8:59 local, and so on). With only
a few weeks of data many station-hour slots have few rows, so a slot with
fewer than MIN_SLOT_ROWS rows falls back to the station's overall training
rate, and an unseen station falls back to the overall training rate.

Both return a probability-like score in [0, 1], so they can be scored with
PR-AUC as well as precision and recall at a threshold.
"""

import pandas as pd

from pogoh.features import LOCAL_TZ
from pogoh.labels import TARGETS

MIN_SLOT_ROWS = 5


def persistence(df, target):
    """Baseline A: 1.0 if the target condition holds now, else 0.0."""
    _, column = TARGETS[target]
    return (df[column] == 0).astype(float)


def hour_of_week(t):
    """0 to 167: day of week (Monday = 0) times 24 plus hour, in local time."""
    local = t.dt.tz_convert(LOCAL_TZ)
    return local.dt.dayofweek * 24 + local.dt.hour


class HourOfWeekRate:
    """Baseline B. Fit on training rows only, then score any rows."""

    def __init__(self, target, min_slot_rows=MIN_SLOT_ROWS):
        self.target = target
        self.min_slot_rows = min_slot_rows

    def fit(self, train):
        rows = train[train[self.target].notna()]
        y = rows[self.target].astype(float)
        how = hour_of_week(rows["t"])
        self.global_rate_ = y.mean()
        self.station_rate_ = y.groupby(rows["station_id"]).mean()
        slots = y.groupby([rows["station_id"], how]).agg(["mean", "size"])
        self.slot_rate_ = slots.loc[slots["size"] >= self.min_slot_rows, "mean"]
        return self

    def predict_proba(self, df):
        keys = pd.MultiIndex.from_arrays([df["station_id"], hour_of_week(df["t"])])
        slot = pd.Series(self.slot_rate_.reindex(keys).to_numpy(), index=df.index)
        station = df["station_id"].map(self.station_rate_)
        return slot.fillna(station).fillna(self.global_rate_).astype(float)
