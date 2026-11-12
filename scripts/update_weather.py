"""Rebuild data/weather.csv: live weather records plus historical backfill.

import_raw.py already does this after importing polls, so you only need
this script to refresh weather on its own:

    .venv\\Scripts\\python scripts\\update_weather.py

It reads live weather records from the data branch as last fetched (run
`git fetch origin data` or import_raw.py first for the newest ones) and
makes one small request to Open-Meteo's historical weather API (ERA5) for hours
the live records do not cover. See src/pogoh/weather.py.
Weather data by Open-Meteo.com, licensed CC BY 4.0.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pogoh.weather import DEFAULT_CACHE_PATH, refresh_weather_cache  # noqa: E402


def main():
    weather = refresh_weather_cache()
    if weather is None:
        print("No polls yet, nothing to match weather to.")
        return 1
    counts = weather["source"].value_counts().to_dict()
    print(f"Saved {len(weather)} hours ({weather['time'].min()} to {weather['time'].max()}) "
          f"to {DEFAULT_CACHE_PATH}: live {counts.get('live', 0)}, backfill {counts.get('backfill', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
