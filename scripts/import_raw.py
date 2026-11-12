"""Import poll records from the `data` branch into the local database.

Run this on the laptop whenever you want fresh data for analysis:

    .venv\\Scripts\\python scripts\\import_raw.py

What it does:
1. `git fetch origin data` to download new records from GitHub.
2. Reads every raw/... file straight from the fetched branch with
   `git archive`, so nothing is checked out and your working folder is
   untouched (see pogoh.raw.read_records_from_branch).
3. Loads records into data/pogoh.db. Polls already in the database are
   skipped, so it is safe to run as often as you like.
4. Rebuilds data/weather.csv from the live weather records on the same
   branch, filling earlier or missed hours from Open-Meteo's historical
   weather API, ERA5 (one small request; see pogoh.weather).

Use --no-fetch to import from what was fetched last time (works offline,
and also skips the weather backfill request). Use --no-weather to skip
step 4.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pogoh.db import DEFAULT_DB_PATH, connect  # noqa: E402
from pogoh.raw import git, import_records, read_records_from_branch  # noqa: E402
from pogoh.weather import DEFAULT_CACHE_PATH, refresh_weather_cache  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Import raw poll records into the local database")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite file to write to")
    parser.add_argument("--no-fetch", action="store_true", help="skip git fetch and the weather backfill")
    parser.add_argument("--no-weather", action="store_true", help="do not rebuild data/weather.csv")
    args = parser.parse_args()

    if not args.no_fetch:
        git("fetch", "--quiet", "origin", "data")

    conn = connect(args.db)
    try:
        imported, skipped = import_records(conn, read_records_from_branch())
        n_ok, n_fail = conn.execute("SELECT SUM(ok), SUM(1 - ok) FROM polls").fetchone()
    finally:
        conn.close()

    print(f"Imported {imported} new polls, skipped {skipped} already stored.")
    print(f"Database now has {n_ok or 0} successful and {n_fail or 0} failed polls.")

    if not (args.no_fetch or args.no_weather):
        weather = refresh_weather_cache(db_path=args.db)
        if weather is not None:
            counts = weather["source"].value_counts().to_dict()
            print(f"Weather: {len(weather)} hours to {weather['time'].max()} "
                  f"(live {counts.get('live', 0)}, backfill {counts.get('backfill', 0)}) "
                  f"in {DEFAULT_CACHE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
