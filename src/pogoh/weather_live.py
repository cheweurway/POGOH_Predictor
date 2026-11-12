"""Live weather snapshots from Open-Meteo, saved as they happen.

The GitHub Actions collector calls fetch_live() about every 15 minutes (see
scripts/weather_to_file.py) and saves the response, unchanged, as one
gzipped JSON file on the `data` branch:

    weather/2026/11/12/20261112T201502_123456Z.json.gz
    {"fetched_at_utc": "...", "ok": true, "error_text": null, "payload": {...}}

Why save it live instead of downloading history later: the record shows
what was known at the time, including the forecast for the next hours, and
it cannot be re-fetched afterwards. A failed fetch also gets a file
(ok=false), so gaps stay visible.

Each response holds hourly rows for the last LIVE_PAST_HOURS hours, the
current hour, and the next LIVE_FORECAST_HOURS hours, plus a "current"
block (Open-Meteo's 15 minute estimate). These are model estimates for one
point in Oakland, not readings from a weather station.

This module needs only `requests` (no pandas), because the cloud runner
installs nothing else. Turning records into a table happens on the laptop,
in pogoh.weather.

Source: https://open-meteo.com, free for non-commercial use under 10,000
calls per day; data licensed CC BY 4.0 (credit in README.md).
"""

from datetime import datetime, timezone
from pathlib import Path

import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
LATITUDE, LONGITUDE = 40.444, -79.953  # Oakland, Pittsburgh
LIVE_PAST_HOURS = 3
LIVE_FORECAST_HOURS = 3
FOLDER = "weather"  # top folder on the data branch
TIME_KEY = "fetched_at_utc"

# Open-Meteo variable name -> our column name (with units).
VARIABLES = {
    "temperature_2m": "temperature_c",
    "apparent_temperature": "apparent_temperature_c",  # "feels like"
    "precipitation": "precipitation_mm",               # rain plus snow, as water
    "rain": "rain_mm",
    "snowfall": "snowfall_cm",
    "wind_speed_10m": "wind_speed_kmh",
    "is_day": "is_day",                                # 1 in daylight, 0 when dark
}


def fetch_live(timeout=20):
    """Current conditions plus recent and upcoming hours. Raises on failure."""
    names = ",".join(VARIABLES)
    response = requests.get(FORECAST_URL, params={
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "hourly": names,
        "current": names,
        "past_hours": LIVE_PAST_HOURS,
        "forecast_hours": LIVE_FORECAST_HOURS,
        "timezone": "UTC",
    }, timeout=timeout)
    response.raise_for_status()
    return response.json()


def make_weather_record(fetched_at_utc, payload=None, error_text=None):
    return {
        TIME_KEY: fetched_at_utc,
        "ok": error_text is None,
        "error_text": error_text,
        "payload": payload,
    }


def weather_relpath(fetched_at_utc):
    """Path of a weather record inside the data branch, from its fetch time."""
    t = datetime.fromisoformat(fetched_at_utc)
    return Path(FOLDER, f"{t:%Y}", f"{t:%m}", f"{t:%d}", f"{t:%Y%m%dT%H%M%S_%f}Z.json.gz")


def fetch_record(fetch=fetch_live, now=None):
    """Fetch once and return a record; a failure becomes an ok=false record."""
    fetched_at = (now or datetime.now(timezone.utc)).isoformat()
    try:
        return make_weather_record(fetched_at, payload=fetch())
    except (requests.RequestException, ValueError) as exc:  # ValueError: bad JSON
        return make_weather_record(fetched_at, error_text=f"{type(exc).__name__}: {exc}")
