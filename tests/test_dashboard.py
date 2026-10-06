"""Tests for the dashboard data shaping functions in pogoh.dashboard.

Each test builds a tiny hand-made table so the expected answer is obvious.
"""

import json

import pandas as pd

from pogoh.dashboard import (
    latest_snapshot, meta, poll_health, rebalancing_events, station_series, stockout_heatmap,
)

T0 = pd.Timestamp("2026-10-05 14:00", tz="UTC")  # 10:00 in Pittsburgh


def obs(rows, start=T0):
    """rows: (station_id, minutes after start, free_bikes, empty_slots)."""
    df = pd.DataFrame(rows, columns=["station_id", "minute", "free_bikes", "empty_slots"])
    df["t"] = start + pd.to_timedelta(df.pop("minute"), unit="min")
    df["normal_bikes"] = df["free_bikes"]
    df["ebikes"] = 0
    return df


def polls(rows, start=T0):
    """rows: (minutes after start, ok)."""
    df = pd.DataFrame(rows, columns=["minute", "ok"])
    df["polled_at_utc"] = start + pd.to_timedelta(df.pop("minute"), unit="min")
    return df


# --- station_series --------------------------------------------------------

def test_gap_inserts_exactly_one_break_and_no_made_up_values():
    df = obs([("A", m, 5, 10) for m in (0, 5, 10, 50, 55)])  # 40 minute gap after 10
    s = station_series(df)["A"]
    assert s["free_bikes"] == [5, 5, 5, None, 5, 5]
    assert s["empty_slots"].count(None) == 1
    assert len(s["t_local"]) == 6
    assert s["t_local"][0] == "2026-10-05T10:00:00"  # local time, not UTC


def test_no_break_for_normal_jitter():
    df = obs([("A", m, 5, 10) for m in (0, 5, 12, 17)])  # 7 minutes is not a gap
    assert None not in station_series(df)["A"]["free_bikes"]


def test_daylight_saving_fold_breaks_the_line():
    # 2026-11-01 05:55 UTC is 01:55 EDT; 06:00 UTC is 01:00 EST (the hour repeats).
    start = pd.Timestamp("2026-11-01 05:50", tz="UTC")
    df = obs([("A", m, 5, 10) for m in (0, 5, 10, 15)], start=start)
    s = station_series(df)["A"]
    assert s["t_local"][1] == "2026-11-01T01:55:00"
    assert s["free_bikes"] == [5, 5, None, 5, 5]
    assert s["t_local"][3] == "2026-11-01T01:00:00"


def test_series_keeps_stations_apart():
    df = obs([("A", 0, 1, 9), ("B", 0, 7, 3), ("A", 5, 2, 8)])
    s = station_series(df)
    assert s["A"]["free_bikes"] == [1, 2] and s["B"]["free_bikes"] == [7]


# --- rebalancing_events ----------------------------------------------------

def test_jump_of_five_is_flagged_and_four_is_not():
    df = obs([("A", 0, 2, 10), ("A", 5, 7, 5), ("A", 10, 3, 9)])  # +5 then -4
    events = rebalancing_events(df)
    assert len(events) == 1
    assert events[0]["before"] == 2 and events[0]["after"] == 7 and events[0]["change"] == 5


def test_jump_across_a_gap_is_not_flagged():
    df = obs([("A", 0, 2, 10), ("A", 40, 8, 4)])  # +6 but 40 minutes apart
    assert rebalancing_events(df) == []


# --- stockout_heatmap ------------------------------------------------------

def test_heatmap_cell_uses_local_hour_across_daylight_saving():
    # 00:00 in Pittsburgh is 04:00 UTC before Nov 1 (EDT) and 05:00 UTC after (EST).
    edt = pd.Timestamp("2026-10-05 04:00", tz="UTC")
    est = pd.Timestamp("2026-11-02 05:00", tz="UTC")
    df = pd.concat([obs([("A", m, 0, 10) for m in (0, 5, 10)], start=edt),
                    obs([("A", m, 0, 10) for m in (0, 5, 10)], start=est)], ignore_index=True)
    h = stockout_heatmap(df)
    row = h["z"][0]
    assert row[0] == 1.0
    assert sum(v is not None for v in row) == 1  # nothing landed in another hour


def test_heatmap_combines_days_rates_blanks_and_order():
    monday = obs([("A", 0, 0, 10), ("A", 5, 3, 7), ("B", 0, 4, 6), ("B", 5, 4, 6)])  # 10:00 local
    tuesday = obs([("A", 0, 0, 10), ("A", 5, 0, 10)], start=T0 + pd.Timedelta(days=1))
    h = stockout_heatmap(pd.concat([monday, tuesday], ignore_index=True))
    assert h["station_ids"] == ["A", "B"]  # highest overall share first
    assert h["overall"] == [0.75, 0.0]
    assert h["hours"] == list(range(24)) and len(h["z"][0]) == 24
    assert h["z"][0][10] == 0.75  # Monday and Tuesday 10:00 combined: 3 of 4 empty
    assert h["z"][1][10] is None  # only 2 observations for B
    assert h["z"][0][9] is None  # no data at 9:00


# --- poll_health -----------------------------------------------------------

def test_health_counts_failures_and_lists_the_incident_gap():
    p = polls([(0, 1), (5, 1), (10, 0), (15, 1), (60, 1), (65, 1)])  # 45 minute gap after 15
    h = poll_health(p)
    assert h["ok"] == [3, 2] and h["failed"] == [1, 0]  # 10:00 hour: 0, 5, 15 ok and 10 failed
    assert h["hours_local"] == ["2026-10-05T10:00:00", "2026-10-05T11:00:00"]
    assert h["gaps"] == [{"start_local": "2026-10-05T10:15:00", "end_local": "2026-10-05T11:00:00", "minutes": 45.0}]


def test_health_includes_empty_hours_and_ignores_failures_for_gaps():
    p = polls([(0, 1), (5, 0), (130, 1)])
    h = poll_health(p)
    assert h["ok"] == [1, 0, 1] and h["failed"] == [1, 0, 0]  # 11:00 had no polls at all
    assert len(h["gaps"]) == 1 and h["gaps"][0]["minutes"] == 130.0  # measured between successes


# --- latest_snapshot and meta ----------------------------------------------

def test_latest_snapshot_picks_newest_row_and_handles_zero_capacity():
    df = obs([("A", 0, 1, 9), ("A", 5, 4, 6), ("B", 0, 0, 0)])
    stations = pd.DataFrame({"station_id": ["A", "B"], "name": ["Alpha", "Beta"],
                             "latitude": [40.44, 40.45], "longitude": [-79.95, -79.96]})
    snap = {r["station_id"]: r for r in latest_snapshot(df, stations)}
    assert snap["A"]["free_bikes"] == 4 and snap["A"]["capacity"] == 10 and snap["A"]["frac_full"] == 0.4
    assert snap["A"]["t_local"] == "2026-10-05T10:05:00"
    assert snap["B"]["frac_full"] is None
    assert snap["A"]["name"] == "Alpha" and snap["A"]["lat"] == 40.44


def test_every_output_is_json_serializable():
    df = obs([("A", 0, 0, 10), ("A", 5, 6, 4), ("A", 10, 0, 10)])
    p = polls([(0, 1), (5, 1), (10, 0)])
    stations = pd.DataFrame({"station_id": ["A"], "name": ["Alpha"], "latitude": [40.4], "longitude": [-79.9]})
    for result in (latest_snapshot(df, stations), station_series(df), rebalancing_events(df),
                   stockout_heatmap(df), poll_health(p), meta(p, df, T0)):
        json.dumps(result, allow_nan=False)  # NaN would be invalid JSON in the browser


def test_meta_counts():
    df = obs([("A", 0, 1, 9), ("B", 0, 2, 8)])
    p = polls([(0, 1), (5, 0)])
    m = meta(p, df, pd.Timestamp("2026-10-05 15:00", tz="UTC"))
    assert m["n_polls_ok"] == 1 and m["n_polls_failed"] == 1 and m["n_stations"] == 2
    assert m["last_poll_utc"] == "2026-10-05T14:00:00+00:00"


def test_empty_inputs_do_not_crash():
    df = obs([])
    p = polls([])
    assert station_series(df) == {} and rebalancing_events(df) == []
    assert poll_health(p)["gaps"] == []
