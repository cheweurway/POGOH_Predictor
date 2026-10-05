"""Poll the API once and save the result as a raw record file.

This is the collector used by the GitHub Actions workflow
(.github/workflows/collect.yml). It needs no database: it writes one gzipped
JSON file under --out, which the workflow then commits to the `data` branch.

A failed poll still writes a file (ok=false with the error text), so gaps
stay visible after import. The exit code is 0 either way, so the workflow
goes on to commit the failure record.

Usage:
    python scripts/poll_to_file.py --out store
"""

import argparse
import sys
from pathlib import Path

# Make src/ importable without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pogoh.collect import FetchFailed, fetch_with_retry  # noqa: E402
from pogoh.raw import make_record, write_record  # noqa: E402


def main(argv=None, fetch=None):
    parser = argparse.ArgumentParser(description="Poll once and save a raw record file")
    parser.add_argument("--out", required=True, help="folder to write raw/... files into")
    args = parser.parse_args(argv)

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
