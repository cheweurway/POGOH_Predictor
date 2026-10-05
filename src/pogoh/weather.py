"""Hourly Pittsburgh weather from the Open-Meteo historical weather API.

Source: https://open-meteo.com (free for non-commercial use, under 10,000
calls per day, data licensed CC BY 4.0 with attribution).

We use one point in Oakland for the whole city. Every station is within a
few kilometers, smaller than the weather model's grid cells.

Open-Meteo hourly rows are labeled by the hour they end on:
- temperature_2m at 10:00 is the temperature at 10:00
- precipitation at 10:00 is the total from 09:00 to 10:00
So for a row at time t, the latest hourly row at or before t uses only
weather that has already happened. That is what weather_features() does.

Recent hours are revised for several days (ERA5 data arrives about 5 days
late), so the local cache re-fetches the last REFRESH_DAYS days each update
and keeps the newest values.
"""

import datetime as dt

import pandas as pd
import requests

from pogoh.db import PROJECT_ROOT

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
LATITUDE, LONGITUDE = 40.444, -79.953  # Oakland, Pittsburgh
DEFAULT_CACHE_PATH = PROJECT_ROOT / "data" / "weather.csv"
REFRESH_DAYS = 7
MAX_AGE = pd.Timedelta(hours=2)  # ignore weather rows older than this


def parse_response(payload):
    """Open-Meteo JSON to a table: time (UTC), temperature_c, precipitation_mm."""
    hourly = payload["hourly"]
    return pd.DataFrame({
        "time": pd.to_datetime(hourly["time"]).tz_localize("UTC"),
        "temperature_c": hourly["temperature_2m"],
        "precipitation_mm": hourly["precipitation"],
    })


def fetch_hourly(start_date, end_date, timeout=30):
    """Hourly weather for an inclusive date range (datetime.date or 'YYYY-MM-DD')."""
    response = requests.get(ARCHIVE_URL, params={
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "hourly": "temperature_2m,precipitation",
        "timezone": "UTC",
    }, timeout=timeout)
    response.raise_for_status()
    return parse_response(response.json())


def load_cache(path=DEFAULT_CACHE_PATH):
    if not path.exists():
        return pd.DataFrame(columns=["time", "temperature_c", "precipitation_mm"])
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True, format="ISO8601")
    return df


def merge_into_cache(cached, fresh):
    """Combine, letting fresh values replace cached ones for the same hour."""
    both = pd.concat([cached, fresh], ignore_index=True)
    both = both.drop_duplicates("time", keep="last").sort_values("time", ignore_index=True)
    return both.dropna(subset=["temperature_c", "precipitation_mm"], how="all")


def update_cache(first_date, path=DEFAULT_CACHE_PATH, today=None, fetch=fetch_hourly):
    """Fetch what is missing or recent, save to the cache, and return it."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    cached = load_cache(path)
    start = first_date
    if len(cached):
        refresh_from = today - dt.timedelta(days=REFRESH_DAYS)
        start = min(max(first_date, refresh_from), cached["time"].max().date())
    fresh = fetch(start, today)
    # The API also returns later hours of today, which are forecasts. Keep
    # only hours that have already happened.
    fresh = fresh[fresh["time"] <= pd.Timestamp.now(tz="UTC")]
    merged = merge_into_cache(cached, fresh)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(path, index=False)
    return merged


def weather_features(t, weather):
    """temperature_c and precipitation_mm from the latest hour at or before each t."""
    left = pd.DataFrame({"row": t.index, "t": t}).sort_values("t")
    right = weather[["time", "temperature_c", "precipitation_mm"]].sort_values("time")
    matched = pd.merge_asof(
        left, right, left_on="t", right_on="time",
        direction="backward", tolerance=MAX_AGE,
    ).set_index("row").reindex(t.index)
    return matched[["temperature_c", "precipitation_mm"]].astype(float)
