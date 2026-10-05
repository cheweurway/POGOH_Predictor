"""Raw poll records: one small gzipped JSON file per poll.

This is how data travels from the GitHub Actions collector to the laptop.
The cloud runner has no database, so each poll is saved as a file on the
repository's `data` branch. On the laptop, import_records() loads those
files into data/pogoh.db through the same code the local collector uses.

Each file holds one record:

    {"polled_at_utc": "...", "ok": true, "error_text": null, "payload": {...}}

where payload is the API response exactly as received (null if the poll
failed). Files are named by poll time, for example
raw/2026/10/05/20261005T140242_183154Z.json.gz, so two polls never collide
and sorting by name sorts by time.
"""

import gzip
import json
from datetime import datetime
from pathlib import Path

from pogoh.collect import record_failure, store_snapshot


def record_relpath(polled_at_utc):
    """Path of a record inside the data branch, derived from its poll time."""
    t = datetime.fromisoformat(polled_at_utc)
    return Path("raw", f"{t:%Y}", f"{t:%m}", f"{t:%d}", f"{t:%Y%m%dT%H%M%S_%f}Z.json.gz")


def make_record(polled_at_utc, payload=None, error_text=None):
    return {
        "polled_at_utc": polled_at_utc,
        "ok": error_text is None,
        "error_text": error_text,
        "payload": payload,
    }


def encode_record(record):
    """Record to gzipped bytes. mtime=0 keeps the output identical for identical input."""
    return gzip.compress(json.dumps(record, separators=(",", ":")).encode("utf-8"), mtime=0)


def decode_record(data):
    return json.loads(gzip.decompress(data).decode("utf-8"))


def write_record(root, record):
    """Save a record under root. Returns the file path."""
    path = Path(root) / record_relpath(record["polled_at_utc"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_record(record))
    return path


def latest_success_time(root):
    """Poll time of the newest successful record under root, or None.

    File names sort by time, so this reads files newest first and stops at
    the first successful one. Failed polls are skipped: a failure never
    reached the API, so it should not delay the next attempt.
    """
    for path in sorted(Path(root).glob("raw/*/*/*/*.json.gz"), reverse=True):
        record = decode_record(path.read_bytes())
        if record["ok"]:
            return datetime.fromisoformat(record["polled_at_utc"])
    return None


def import_records(conn, records):
    """Load records into the database, skipping polls already stored.

    A poll counts as already stored if the polls table has a row with the same
    polled_at_utc, so running the import twice never duplicates data.
    Returns (n_imported, n_skipped).
    """
    seen = {row[0] for row in conn.execute("SELECT polled_at_utc FROM polls")}
    imported = skipped = 0
    for record in sorted(records, key=lambda r: r["polled_at_utc"]):
        polled_at = record["polled_at_utc"]
        if polled_at in seen:
            skipped += 1
            continue
        if record["ok"]:
            store_snapshot(conn, record["payload"], polled_at)
        else:
            record_failure(conn, polled_at, record["error_text"] or "unknown error")
        seen.add(polled_at)
        imported += 1
    return imported, skipped
