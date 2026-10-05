"""Tests for future-state labels.

Each test builds a tiny hand-made table so the expected answer is obvious.
"""

import pandas as pd

from pogoh.labels import add_labels

T0 = pd.Timestamp("2026-10-05 14:00", tz="UTC")


def obs(rows):
    """rows: (station_id, minutes after T0, free_bikes, empty_slots)."""
    df = pd.DataFrame(rows, columns=["station_id", "minute", "free_bikes", "empty_slots"])
    df["t"] = T0 + pd.to_timedelta(df.pop("minute"), unit="min")
    return df


def label_at(df, minute, station="A", col="stockout_30"):
    labeled = add_labels(df)
    row = labeled[(labeled["station_id"] == station) & (labeled["t"] == T0 + pd.Timedelta(minutes=minute))]
    assert len(row) == 1
    return row.iloc[0][col]


def test_exact_match():
    df = obs([("A", 0, 5, 10), ("A", 30, 0, 15)])
    assert label_at(df, 0) == 1


def test_match_within_three_minutes():
    df = obs([("A", 0, 5, 10), ("A", 32.5, 0, 15)])
    assert label_at(df, 0) == 1


def test_no_match_beyond_three_minutes_is_missing():
    df = obs([("A", 0, 5, 10), ("A", 33.5, 0, 15), ("A", 26, 0, 15)])
    assert pd.isna(label_at(df, 0))


def test_nearest_of_two_candidates_wins():
    # 29 min (2 bikes) is closer to 30 than 32 min (0 bikes).
    df = obs([("A", 0, 5, 10), ("A", 29, 2, 13), ("A", 32, 0, 15)])
    assert label_at(df, 0) == 0
    # And the other way round.
    df = obs([("A", 0, 5, 10), ("A", 28, 2, 13), ("A", 31, 0, 15)])
    assert label_at(df, 0) == 1


def test_never_matches_another_station():
    df = obs([("A", 0, 5, 10), ("B", 30, 0, 15)])
    assert pd.isna(label_at(df, 0))


def test_sixty_minute_and_dock_full_labels():
    df = obs([("A", 0, 5, 10), ("A", 30, 3, 0), ("A", 60, 0, 15)])
    assert label_at(df, 0, col="stockout_30") == 0
    assert label_at(df, 0, col="dock_full_30") == 1
    assert label_at(df, 0, col="stockout_60") == 1


def test_labels_are_independent_per_horizon():
    df = obs([("A", 0, 5, 10), ("A", 30, 0, 15)])  # nothing near 60 minutes
    assert label_at(df, 0, col="stockout_30") == 1
    assert pd.isna(label_at(df, 0, col="stockout_60"))


def test_transition_flag():
    df = obs([
        ("A", 0, 5, 10), ("A", 30, 0, 15),   # bikes now, empty later: transition
        ("B", 0, 0, 15), ("B", 30, 0, 15),   # empty now, empty later: no transition
    ])
    assert label_at(df, 0, "A", "stockout_30_transition") == 1
    assert label_at(df, 0, "B", "stockout_30_transition") == 0


def test_matched_time_is_reported():
    df = obs([("A", 0, 5, 10), ("A", 31, 0, 15)])
    assert label_at(df, 0, col="stockout_30_matched_t") == T0 + pd.Timedelta(minutes=31)


def test_input_order_does_not_matter():
    df = obs([("B", 30, 0, 15), ("A", 30, 0, 15), ("A", 0, 5, 10), ("B", 0, 5, 10)])
    labeled = add_labels(df)
    assert labeled.index.equals(df.index)  # rows keep their original positions
    assert label_at(df, 0, "A") == 1 and label_at(df, 0, "B") == 1
