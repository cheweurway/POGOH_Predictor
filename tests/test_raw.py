"""Tests for raw record files and importing them into the database."""

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest
import requests

from pogoh.db import connect
from pogoh.raw import (
    decode_record, encode_record, import_records, latest_success_time, make_record, record_relpath, write_record,
)

FIXTURE = Path(__file__).parent / "fixtures" / "snapshot_sample.json"

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "poll_to_file.py"
_spec = importlib.util.spec_from_file_location("poll_to_file", _SCRIPT)
poll_to_file = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(poll_to_file)


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text())


def test_record_path_is_sorted_by_time():
    path = record_relpath("2026-10-05T14:02:42.183154+00:00")
    assert path.as_posix() == "raw/2026/10/05/20261005T140242_183154Z.json.gz"


def test_encode_decode_round_trip(payload):
    record = make_record("2026-10-05T14:02:42.183154+00:00", payload=payload)
    assert decode_record(encode_record(record)) == record


def test_failure_record():
    record = make_record("2026-10-05T14:02:42+00:00", error_text="ConnectionError: down")
    assert record["ok"] is False and record["payload"] is None


def test_import_stores_successes_and_failures(payload):
    conn = connect(":memory:")
    records = [
        make_record("2026-10-05T14:05:00+00:00", error_text="ConnectionError: down"),
        make_record("2026-10-05T14:00:00+00:00", payload=payload),
    ]

    assert import_records(conn, records) == (2, 0)

    assert conn.execute("SELECT polled_at_utc, ok FROM polls ORDER BY poll_id").fetchall() == [
        ("2026-10-05T14:00:00+00:00", 1),  # imported in time order
        ("2026-10-05T14:05:00+00:00", 0),
    ]
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 3


def test_import_twice_does_not_duplicate(payload):
    conn = connect(":memory:")
    records = [make_record("2026-10-05T14:00:00+00:00", payload=payload)]
    import_records(conn, records)

    assert import_records(conn, records) == (0, 1)
    assert conn.execute("SELECT COUNT(*) FROM polls").fetchone()[0] == 1


def test_poll_to_file_writes_a_success_record(tmp_path, payload):
    assert poll_to_file.main(["--out", str(tmp_path)], fetch=lambda: payload) == 0

    files = list(tmp_path.rglob("*.json.gz"))
    assert len(files) == 1
    record = decode_record(files[0].read_bytes())
    assert record["ok"] is True and record["payload"] == payload


def test_poll_to_file_writes_a_failure_record(tmp_path):
    def server_error():
        raise requests.HTTPError("503 Service Unavailable")  # not retried, so no waiting

    assert poll_to_file.main(["--out", str(tmp_path)], fetch=server_error) == 0

    record = decode_record(next(tmp_path.rglob("*.json.gz")).read_bytes())
    assert record["ok"] is False and "503" in record["error_text"]


def test_write_record_uses_the_dated_path(tmp_path, payload):
    record = make_record("2026-10-05T14:00:00+00:00", payload=payload)
    path = write_record(tmp_path, record)
    assert path == tmp_path / record_relpath(record["polled_at_utc"])


# --- minimum gap guard in poll_to_file ------------------------------------

def _never_called():
    raise AssertionError("fetch should not run when the guard skips")


def test_latest_success_time_skips_failures(tmp_path, payload):
    write_record(tmp_path, make_record("2026-10-05T14:00:00+00:00", payload=payload))
    write_record(tmp_path, make_record("2026-10-05T14:05:00+00:00", error_text="ConnectionError: down"))
    assert latest_success_time(tmp_path) == datetime.fromisoformat("2026-10-05T14:00:00+00:00")
    assert latest_success_time(tmp_path / "empty") is None


def test_guard_skips_a_poll_too_soon_after_the_last_success(tmp_path, payload):
    write_record(tmp_path, make_record("2026-10-05T14:00:00+00:00", payload=payload))
    now = datetime.fromisoformat("2026-10-05T14:01:00+00:00")  # 60 s later, like a bunched run

    assert poll_to_file.main(["--out", str(tmp_path)], fetch=_never_called, now=now) == 0
    assert len(list(tmp_path.rglob("*.json.gz"))) == 1  # nothing new written


def test_guard_allows_a_poll_after_four_minutes(tmp_path, payload):
    write_record(tmp_path, make_record("2026-10-05T14:00:00+00:00", payload=payload))
    now = datetime.fromisoformat("2026-10-05T14:05:00+00:00")

    poll_to_file.main(["--out", str(tmp_path)], fetch=lambda: payload, now=now)
    assert len(list(tmp_path.rglob("*.json.gz"))) == 2


def test_recent_failure_does_not_block_the_next_poll(tmp_path, payload):
    write_record(tmp_path, make_record("2026-10-05T14:00:00+00:00", payload=payload))
    write_record(tmp_path, make_record("2026-10-05T14:05:00+00:00", error_text="ConnectionError: down"))
    now = datetime.fromisoformat("2026-10-05T14:06:00+00:00")  # 6 min after the last success

    poll_to_file.main(["--out", str(tmp_path)], fetch=lambda: payload, now=now)
    assert len(list(tmp_path.rglob("*.json.gz"))) == 3
