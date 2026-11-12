"""Hourly Pittsburgh weather table and weather features.

Two sources fill one hourly table (data/weather.csv, not committed):

- live: snapshots the cloud collector saves about every 15 minutes on the
  `data` branch (see pogoh.weather_live). For each hour we keep the value
  from the first snapshot taken after that hour ended, which is what was
  known at the time.
- backfill: Open-Meteo's historical weather API (ERA5 reanalysis), for
  hours before live collection started or missed by it. Its newest days
  are provisional and get revised, so each update re-fetches the last
  REFRESH_DAYS days.

  Checked 2026-11-12: Open-Meteo's historical *forecast* API was tried as
  the backfill and rejected. Its temperature matched ERA5 (correlation
  0.98) but its precipitation did not (0.11): it lumped rain into single
  hours, for example 61.9 mm in one hour on 2026-10-14 where ERA5 has
  17.7 mm spread over the day.

Live values win over backfill for the same hour. A `source` column records
which one each hour came from.

Open-Meteo hourly rows are labeled by the hour they end on:
- temperature at 10:00 is the temperature at 10:00
- precipitation at 10:00 is the total from 09:00 to 10:00
So for a row at time t, the latest hourly row at or before t uses only
weather that has already happened. That is what weather_features() does.

We use one point in Oakland for the whole city. Every station is within a
few kilometers, smaller than the weather model's grid cells.

Source: https://open-meteo.com (free for non-commercial use, under 10,000
calls per day, data licensed CC BY 4.0 with attribution).
"""

import datetime as dt
import sqlite3

import pandas as pd
import requests

from pogoh.db import DEFAULT_DB_PATH, PROJECT_ROOT
from pogoh.raw import DATA_REF, read_records_from_branch
from pogoh.weather_live import FOLDER, LATITUDE, LONGITUDE, TIME_KEY, VARIABLES

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DEFAULT_CACHE_PATH = PROJECT_ROOT / "data" / "weather.csv"
COLUMNS = list(VARIABLES.values())
CACHE_COLUMNS = ["time", *COLUMNS, "source"]
REFRESH_DAYS = 7  # re-fetch this many recent days of backfill (ERA5 gets revised)
MAX_AGE = pd.Timedelta(hours=2)  # ignore weather rows older than this
DRY_HOURS_CAP = 72  # hours_since_precip stops counting here
SOURCE_PRIORITY = {"backfill": 0, "live": 1}


def parse_response(payload, source="backfill"):
    """Open-Meteo JSON to a table: time (UTC), one column per variable, source.

    Variables missing from the response become NaN.
    """
    hourly = payload["hourly"]
    df = pd.DataFrame({"time": pd.to_datetime(hourly["time"]).tz_localize("UTC")})
    for api_name, column in VARIABLES.items():
        values = hourly.get(api_name)
        df[column] = pd.Series(values, dtype=float) if values is not None else float("nan")
    df["source"] = source
    return df


def fetch_backfill(start_date, end_date, timeout=30):
    """Hourly weather for an inclusive date range (datetime.date or 'YYYY-MM-DD')."""
    response = requests.get(ARCHIVE_URL, params={
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "hourly": ",".join(VARIABLES),
        "timezone": "UTC",
    }, timeout=timeout)
    response.raise_for_status()
    return parse_response(response.json(), source="backfill")


def live_hourly(records):
    """Hourly table from live snapshot records.

    From each successful snapshot, keep only hours that had already ended
    when it was fetched (later rows are forecasts). For each hour keep the
    value from the earliest such snapshot: what was known right after it.
    """
    frames = []
    for record in records:
        if not record["ok"]:
            continue
        fetched_at = pd.Timestamp(record[TIME_KEY])
        df = parse_response(record["payload"], source="live")
        df = df[df["time"] <= fetched_at]
        frames.append(df.assign(fetched_at=fetched_at))
    if not frames:
        return pd.DataFrame(columns=CACHE_COLUMNS)
    both = pd.concat(frames, ignore_index=True).sort_values(["time", "fetched_at"])
    first = both.drop_duplicates("time", keep="first")
    return first[CACHE_COLUMNS].reset_index(drop=True)


def load_cache(path=DEFAULT_CACHE_PATH):
    """The cached table, or an empty one.

    A cache in an older layout (missing columns) is ignored, so the next
    update rebuilds it from scratch.
    """
    if not path.exists():
        return pd.DataFrame(columns=CACHE_COLUMNS)
    df = pd.read_csv(path)
    if not set(CACHE_COLUMNS) <= set(df.columns):
        return pd.DataFrame(columns=CACHE_COLUMNS)
    df["time"] = pd.to_datetime(df["time"], utc=True, format="ISO8601")
    return df[CACHE_COLUMNS]


def merge_sources(*tables):
    """Combine tables into one row per hour.

    Live beats backfill for the same hour; within a source, the later table
    wins (so a fresh backfill replaces an older one).
    """
    tables = [t for t in tables if len(t)]
    if not tables:
        return pd.DataFrame(columns=CACHE_COLUMNS)
    both = pd.concat([t[CACHE_COLUMNS].assign(order=i) for i, t in enumerate(tables)],
                     ignore_index=True)
    both["priority"] = both["source"].map(SOURCE_PRIORITY)
    both = both.sort_values(["time", "priority", "order"], kind="stable")
    merged = both.drop_duplicates("time", keep="last").sort_values("time", ignore_index=True)
    merged = merged.dropna(subset=COLUMNS, how="all")
    return merged[CACHE_COLUMNS]


def update_cache(first_date, path=DEFAULT_CACHE_PATH, today=None, now=None,
                 fetch=fetch_backfill, live=None):
    """Fetch missing or recent backfill, overlay live hours, save, and return it."""
    now = now or pd.Timestamp.now(tz="UTC")
    today = today or now.date()
    cached = load_cache(path)
    start = first_date
    if len(cached):
        refresh_from = today - dt.timedelta(days=REFRESH_DAYS)
        start = min(max(first_date, refresh_from), cached["time"].max().date())
    fresh = fetch(start, today)
    # The API also returns later hours of today, which are forecasts. Keep
    # only hours that have already happened.
    fresh = fresh[fresh["time"] <= now]
    merged = merge_sources(cached, fresh, live if live is not None else pd.DataFrame())
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(path, index=False)
    return merged


def refresh_weather_cache(db_path=DEFAULT_DB_PATH, ref=DATA_REF, path=DEFAULT_CACHE_PATH):
    """Rebuild data/weather.csv from the fetched data branch plus backfill.

    Covers the first collected poll through today. Returns the table, or None
    if there are no polls yet. The data branch must already be fetched.
    """
    conn = sqlite3.connect(db_path)
    try:
        first = conn.execute("SELECT MIN(polled_at_utc) FROM polls").fetchone()[0]
    finally:
        conn.close()
    if first is None:
        return None
    live = live_hourly(read_records_from_branch(ref, folder=FOLDER))
    return update_cache(pd.Timestamp(first).date(), path=path, live=live)


def _derived(weather):
    """Per hourly row: precipitation over the last 3 hours, hours since precipitation.

    Both look only at rows at or before that row's hour.
    """
    w = weather.sort_values("time").set_index("time")
    precip = w["precipitation_mm"]
    # Rows in (h - 3h, h]: the hours ending h-2, h-1 and h, i.e. h-3 to h.
    w["precip_3h_mm"] = precip.rolling("3h", closed="right").sum()
    wet_time = pd.Series(w.index.where(precip > 0), index=w.index).ffill()
    hours = (w.index.to_series() - wet_time).dt.total_seconds() / 3600
    w["hours_since_precip"] = hours.fillna(DRY_HOURS_CAP).clip(upper=DRY_HOURS_CAP)
    return w.reset_index()


FEATURE_COLUMNS = [*COLUMNS, "precip_3h_mm", "hours_since_precip"]


def weather_features(t, weather):
    """Weather columns from the latest hour at or before each t (at most MAX_AGE old)."""
    left = pd.DataFrame({"row": t.index, "t": t}).sort_values("t")
    right = _derived(weather)[["time", *FEATURE_COLUMNS]]
    matched = pd.merge_asof(
        left, right, left_on="t", right_on="time",
        direction="backward", tolerance=MAX_AGE,
    ).set_index("row").reindex(t.index)
    return matched[FEATURE_COLUMNS].astype(float)
