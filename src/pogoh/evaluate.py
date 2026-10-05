"""Splitting data by time (metrics and error analysis come in milestone 3).

time_split() puts the oldest 70 percent of rows in train, the next 15
percent in validation, and the newest 15 percent in test. Rows are never
shuffled across time.

A gap (default 60 minutes, the longest label horizon) is left before each
boundary. Without it, a training row at 11:50 with a 60 minute label would
be labeled with data from 12:50, inside the validation period. Rows that
fall in a gap are dropped.
"""

import pandas as pd

SPLIT_FRACTIONS = (0.70, 0.15, 0.15)
SPLIT_GAP = pd.Timedelta(minutes=60)


def time_split(df, fractions=SPLIT_FRACTIONS, gap=SPLIT_GAP):
    """Return a Series of 'train', 'validation', 'test', or NA (gap), aligned to df."""
    if abs(sum(fractions) - 1) > 1e-9:
        raise ValueError("split fractions must add up to 1")
    t = df["t"]
    val_start = t.quantile(fractions[0])
    test_start = t.quantile(fractions[0] + fractions[1])

    split = pd.Series(pd.NA, index=df.index, dtype="string")
    split[t < val_start - gap] = "train"
    split[(t >= val_start) & (t < test_start - gap)] = "validation"
    split[t >= test_start] = "test"
    return split
