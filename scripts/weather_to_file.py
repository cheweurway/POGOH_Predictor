"""Fetch live weather once and save it as a record file under --out/weather/.

Run by the GitHub Actions collector (.github/workflows/collect.yml) right
after each bike poll. That workflow runs every 5 minutes, but this script
skips unless the newest successful weather record is at least
MIN_GAP_SECONDS old, so weather is saved about every 15 minutes.

A failed fetch still writes a file (ok=false), so gaps stay visible. The
exit code is 0 either way. The workflow also marks this step
continue-on-error, so a weather problem can never stop bike polls.

Needs only `requests` (see pogoh.weather_live).

Usage:
    python scripts/weather_to_file.py --out store
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pogoh.raw import encode_record, latest_success_time  # noqa: E402
from pogoh.weather_live import FOLDER, TIME_KEY, fetch_live, fetch_record, weather_relpath  # noqa: E402

MIN_GAP_SECONDS = 14 * 60  # about every 15 minutes, given runs every 5


def main(argv=None, fetch=fetch_live, now=None):
    parser = argparse.ArgumentParser(description="Fetch live weather once and save a record file")
    parser.add_argument("--out", required=True, help="folder to write weather/... files into")
    args = parser.parse_args(argv)

    now = now or datetime.now(timezone.utc)
    last = latest_success_time(args.out, folder=FOLDER, time_key=TIME_KEY)
    if last is not None and (now - last).total_seconds() < MIN_GAP_SECONDS:
        print(f"SKIP last weather record was {(now - last).total_seconds():.0f}s ago")
        return 0

    record = fetch_record(fetch, now=now)
    path = Path(args.out) / weather_relpath(record[TIME_KEY])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_record(record))
    print(f"{'OK' if record['ok'] else 'FAIL ' + record['error_text']} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
