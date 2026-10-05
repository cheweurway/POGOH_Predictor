"""Run one poll of the Pogoh API and exit.

Windows Task Scheduler calls this every 5 minutes. Running once per call
(instead of a long-lived loop) means a laptop sleep or crash only costs the
polls that were missed, and the next scheduled run starts clean.

Safety rule: if the last successful poll was less than MIN_GAP_SECONDS ago,
this run skips, so we can never poll faster than intended even if the task
fires twice or someone runs the script by hand.

Usage:
    .venv\\Scripts\\python scripts\\run_collector.py [--db PATH]

Exit code is 0 on success or a deliberate skip, 1 if the poll failed.
Each run appends one line to data/collector.log.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make src/ importable even if the package was not installed with pip.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pogoh.collect import poll_once, utc_now_iso  # noqa: E402
from pogoh.db import DEFAULT_DB_PATH, connect  # noqa: E402

MIN_GAP_SECONDS = 240  # 4 minutes, leaving slack for scheduler jitter
LOG_PATH = DEFAULT_DB_PATH.parent / "collector.log"


def seconds_since_last_poll(conn):
    """Seconds since the last successful poll, or None if there is none.

    Failed polls are ignored. A failed connection never reached the API, so
    it should not block the next run from trying again.
    """
    row = conn.execute("SELECT MAX(polled_at_utc) FROM polls WHERE ok = 1").fetchone()
    if row[0] is None:
        return None
    last = datetime.fromisoformat(row[0])
    return (datetime.now(timezone.utc) - last).total_seconds()


def log(message):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"{utc_now_iso()} {message}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite file to write to")
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        gap = seconds_since_last_poll(conn)
        if gap is not None and gap < MIN_GAP_SECONDS:
            log(f"SKIP last poll was {gap:.0f}s ago")
            return 0

        poll_id, ok = poll_once(conn)
        if ok:
            n = conn.execute("SELECT n_stations FROM polls WHERE poll_id = ?", (poll_id,)).fetchone()[0]
            log(f"OK poll_id={poll_id} stations={n}")
            return 0
        err = conn.execute("SELECT error_text FROM polls WHERE poll_id = ?", (poll_id,)).fetchone()[0]
        log(f"FAIL poll_id={poll_id} {err}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
