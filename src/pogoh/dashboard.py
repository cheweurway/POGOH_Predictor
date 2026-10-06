"""Shape collected data into small, JSON-ready pieces for the dashboard.

Every function here is pure: it takes pandas tables (from pogoh.data) and
returns plain Python lists and dicts, with None wherever a value is missing,
so the result can go straight into json.dump. Nothing reads files or the
network, which keeps the functions easy to test.

One gap rule is used everywhere: two successive observations more than GAP
apart (10 minutes, so at least one missed poll beyond normal jitter) are
treated as a gap. Charts break the line there, the health table lists it,
and a bike count change across it is not called a rebalancing event.

All times shown to people are in Pittsburgh time (TZ). Times are converted
from UTC with tz_convert, which handles the daylight saving change on
2026-11-01.
"""

import math

import pandas as pd

TZ = "America/New_York"
GAP = pd.Timedelta(minutes=10)
REBALANCE_JUMP = 5  # bikes between successive polls
HEATMAP_MIN_OBS = 3  # fewer observations than this leaves a heatmap cell blank
LOCAL_FORMAT = "%Y-%m-%dT%H:%M:%S"  # wall-clock time, no offset (for charts)


def _clean(value):
    """NaN or NA to None, numpy numbers to plain Python numbers."""
    if value is None or (isinstance(value, float) and math.isnan(value)) or value is pd.NA:
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _local_labels(t):
    return t.dt.tz_convert(TZ).dt.strftime(LOCAL_FORMAT)


def _utc_offset_minutes(t):
    """Offset of local time from UTC, in minutes, for each timestamp."""
    local_wall = t.dt.tz_convert(TZ).dt.tz_localize(None)
    utc_wall = t.dt.tz_convert("UTC").dt.tz_localize(None)
    return (local_wall - utc_wall).dt.total_seconds() / 60


def latest_snapshot(obs, stations):
    """The newest observation of every station, with its name and location.

    Used for the map when the live layer cannot reach GitHub. capacity is
    free_bikes + empty_slots; frac_full is None when capacity is 0.
    """
    newest = obs.sort_values("t").groupby("station_id").tail(1)
    merged = newest.merge(stations, on="station_id", how="left").sort_values("name")
    rows = []
    for r in merged.itertuples(index=False):
        capacity = int(r.free_bikes) + int(r.empty_slots)
        rows.append({
            "station_id": r.station_id,
            "name": r.name,
            "lat": _clean(r.latitude),
            "lon": _clean(r.longitude),
            "free_bikes": int(r.free_bikes),
            "normal_bikes": _clean(r.normal_bikes),
            "ebikes": _clean(r.ebikes),
            "empty_slots": int(r.empty_slots),
            "capacity": capacity,
            "frac_full": r.free_bikes / capacity if capacity else None,
            "t_utc": r.t.isoformat(),
            "t_local": r.t.tz_convert(TZ).strftime(LOCAL_FORMAT),
        })
    return rows


def station_series(obs, gap=GAP):
    """Per station: local times with free_bikes and empty_slots, ready to plot.

    Where two successive rows are more than `gap` apart, or where the clock
    changes for daylight saving, a break point (y values None) is inserted
    so the chart line stops instead of bridging the gap or zigzagging back
    through the repeated hour. Nothing is interpolated.
    """
    out = {}
    for station_id, g in obs.sort_values("t").groupby("station_id", sort=True):
        t = g["t"].reset_index(drop=True)
        offset = _utc_offset_minutes(t)
        breaks = (t.diff() > gap) | (offset.diff().fillna(0) != 0)
        labels = _local_labels(t)
        xs, free, empty = [], [], []
        for i, (is_break, x, f, e) in enumerate(zip(breaks, labels, g["free_bikes"], g["empty_slots"])):
            if is_break:
                xs.append(labels.iloc[i - 1])
                free.append(None)
                empty.append(None)
            xs.append(x)
            free.append(int(f))
            empty.append(int(e))
        out[station_id] = {"t_local": xs, "free_bikes": free, "empty_slots": empty}
    return out


def rebalancing_events(obs, gap=GAP, jump=REBALANCE_JUMP):
    """Suspected rebalancing: free_bikes changes by `jump` or more between
    successive polls of a station that are not separated by a gap.

    "Suspected" because a burst of riders can also do this.
    """
    events = []
    for station_id, g in obs.sort_values("t").groupby("station_id", sort=True):
        dt = g["t"].diff()
        change = g["free_bikes"].diff()
        hits = g[(dt <= gap) & (change.abs() >= jump)]
        for idx, row in hits.iterrows():
            events.append({
                "station_id": station_id,
                "t_local": row["t"].tz_convert(TZ).strftime(LOCAL_FORMAT),
                "before": int(row["free_bikes"] - change[idx]),
                "after": int(row["free_bikes"]),
                "change": int(change[idx]),
            })
    return events


def hour_of_day_local(t):
    """0 to 23: the hour of the day in Pittsburgh time."""
    return t.dt.tz_convert(TZ).dt.hour


def stockout_heatmap(obs, min_obs=HEATMAP_MIN_OBS):
    """Share of observations with free_bikes == 0, per station and hour of day.

    Hours are Pittsburgh time with all days combined, so the grid fills in
    after a single day of data. Cells with fewer than `min_obs` observations
    are None (blank). Stations are sorted by overall stockout share, highest
    first, so the page can show the top rows only. Shares count
    observations, not minutes, so periods with more polls weigh more.
    """
    empty = (obs["free_bikes"] == 0).astype(float)
    hour = hour_of_day_local(obs["t"])
    cells = empty.groupby([obs["station_id"], hour]).agg(["mean", "size"])
    rates = cells["mean"].where(cells["size"] >= min_obs).unstack()
    rates = rates.reindex(columns=range(24))
    overall = empty.groupby(obs["station_id"]).mean().sort_values(ascending=False, kind="stable")
    rates = rates.reindex(overall.index)
    return {
        "station_ids": list(overall.index),
        "overall": [_clean(v) for v in overall],
        "hours": list(range(24)),
        "z": [[_clean(v) for v in row] for row in rates.to_numpy()],
    }


def poll_health(polls, gap=GAP):
    """Polls per hour (ok and failed) and the gaps between successful polls.

    Hours are counted in UTC and labeled in local time, so the repeated hour
    on the daylight saving day gets two bars with the same label rather than
    being merged. Hours with no polls at all are included with zeros.
    """
    t = polls["polled_at_utc"]
    hour = t.dt.floor("h")
    ok = polls["ok"].astype(bool)
    counts = pd.DataFrame({"hour": hour, "ok": ok.astype(int), "failed": (~ok).astype(int)})
    per_hour = counts.groupby("hour")[["ok", "failed"]].sum()
    if len(per_hour):
        per_hour = per_hour.reindex(pd.date_range(per_hour.index.min(), per_hour.index.max(), freq="h"), fill_value=0)
    labels = pd.Series(per_hour.index).dt.tz_convert(TZ).dt.strftime(LOCAL_FORMAT)

    good = t[ok].sort_values().reset_index(drop=True)
    gaps = []
    for start, end in zip(good[:-1], good[1:]):
        if end - start > gap:
            gaps.append({
                "start_local": start.tz_convert(TZ).strftime(LOCAL_FORMAT),
                "end_local": end.tz_convert(TZ).strftime(LOCAL_FORMAT),
                "minutes": round((end - start).total_seconds() / 60, 1),
            })
    return {
        "hours_local": list(labels),
        "ok": [int(v) for v in per_hour["ok"]],
        "failed": [int(v) for v in per_hour["failed"]],
        "gaps": gaps,
        "gap_minutes": gap.total_seconds() / 60,
    }


def meta(polls, obs, built_at):
    """Summary numbers and credits shown in the page header and footer."""
    ok = polls["ok"].astype(bool)
    good = polls.loc[ok, "polled_at_utc"]
    return {
        "built_at_utc": built_at.isoformat(),
        "first_poll_utc": good.min().isoformat() if len(good) else None,
        "last_poll_utc": good.max().isoformat() if len(good) else None,
        "n_polls_ok": int(ok.sum()),
        "n_polls_failed": int((~ok).sum()),
        "n_stations": int(obs["station_id"].nunique()),
        "n_observations": int(len(obs)),
        "gap_minutes": GAP.total_seconds() / 60,
        "timezone": TZ,
        "credits": "Station data: CityBikes (citybik.es), built on PyBikes.",
    }
