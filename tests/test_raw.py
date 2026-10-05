"""Tests for raw record files and importing them into the database."""

import importlib.util
import json
from pathlib import Path

import pytest
import requests

from pogoh.db import connect
from pogoh.raw import decode_record, encode_record, import_records, make_record, record_relpath, write_record

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
