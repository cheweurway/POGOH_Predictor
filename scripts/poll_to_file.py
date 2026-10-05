"""Poll the API once and save the result as a raw record file.

This is the collector used by the GitHub Actions workflow
(.github/workflows/collect.yml). It needs no database: it writes one gzipped
JSON file under --out, which the workflow then commits to the `data` branch.

A failed poll still writes a file (ok=false with the error text), so gaps
stay visible after import. The exit code is 0 either way, so the workflow
goes on to commit the failure record.

Safety rule: if the newest successful record under --out is less than
MIN_GAP_SECONDS old, this run skips and writes nothing. GitHub sometimes
holds runs in a queue and then starts two close together; without this
guard they would poll a minute apart.

Usage:
    python scripts/poll_to_file.py --out store
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make src/ importable without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pogoh.collect import FetchFailed, fetch_with_retry  # noqa: E402
from pogoh.raw import latest_success_time, make_record, write_record  # noqa: E402

MIN_GAP_SECONDS = 240  # 4 minutes, leaving slack for trigger jitter


def main(argv=None, fetch=None, now=None):
    parser = argparse.ArgumentParser(description="Poll once and save a raw record file")
    parser.add_argument("--out", required=True, help="folder to write raw/... files into")
    args = parser.parse_args(argv)

    last = latest_success_time(args.out)
    gap = ((now or datetime.now(timezone.utc)) - last).total_seconds() if last else None
    if gap is not None and gap < MIN_GAP_SECONDS:
        print(f"SKIP last successful poll was {gap:.0f}s ago")
        return 0

    try:
        payload, polled_at = fetch_with_retry(fetch) if fetch else fetch_with_retry()
        record = make_record(polled_at, payload=payload)
        status = f"OK stations={len(payload['network']['stations'])}"
    except FetchFailed as exc:
        record = make_record(exc.polled_at_utc, error_text=str(exc))
        status = f"FAIL {exc}"

    path = write_record(args.out, record)
    print(f"{status} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
