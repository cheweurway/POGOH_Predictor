"""Download hourly Pittsburgh weather into data/weather.csv.

Covers the first collected poll through today. Run it before building
features, for example right after import_raw.py:

    .venv\\Scripts\\python scripts\\update_weather.py

One small request to Open-Meteo per run, well under their free limits.
Weather data by Open-Meteo.com, licensed CC BY 4.0.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pogoh.db import DEFAULT_DB_PATH  # noqa: E402
from pogoh.data import load_polls  # noqa: E402
from pogoh.weather import DEFAULT_CACHE_PATH, update_cache  # noqa: E402


def main():
    polls = load_polls(sqlite3.connect(DEFAULT_DB_PATH))
    if polls.empty:
        print("No polls yet, nothing to match weather to.")
        return 1
    first_date = polls["polled_at_utc"].min().date()
    weather = update_cache(first_date)
    print(f"Saved {len(weather)} hours ({weather['time'].min()} to {weather['time'].max()}) to {DEFAULT_CACHE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
