"""Tests for storing a snapshot, using a saved API response.

The fixture tests/fixtures/snapshot_sample.json is a real response trimmed to
three stations: one with bikes and docks, one with 0 free bikes, and one with
0 empty slots. No test here calls the network.
"""

import json
from pathlib import Path

import pytest
import requests

from pogoh.collect import normalize_source_timestamp, poll_once, store_snapshot
from pogoh.db import connect

FIXTURE = Path(__file__).parent / "fixtures" / "snapshot_sample.json"
POLLED_AT = "2026-10-05T00:50:30+00:00"


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def conn():
    return connect(":memory:")


def test_store_snapshot_writes_one_poll_and_all_stations(conn, payload):
    poll_id = store_snapshot(conn, payload, POLLED_AT)

    poll = conn.execute("SELECT polled_at_utc, ok, n_stations, error_text FROM polls").fetchall()
    assert poll == [(POLLED_AT, 1, 3, None)]
    assert conn.execute("SELECT COUNT(*) FROM stations").fetchone()[0] == 3
    n_obs = conn.execute("SELECT COUNT(*) FROM observations WHERE poll_id = ?", (poll_id,)).fetchone()[0]
    assert n_obs == 3


def test_stored_values_match_the_payload(conn, payload):
    store_snapshot(conn, payload, POLLED_AT)
    for station in payload["network"]["stations"]:
        row = conn.execute(
            """SELECT free_bikes, empty_slots, normal_bikes, ebikes, slots,
                      is_renting, is_returning, source_last_updated
               FROM observations WHERE station_id = ?""",
            (station["id"],),
        ).fetchone()
        extra = station["extra"]
        assert row == (
            station["free_bikes"],
            station["empty_slots"],
            extra["normal_bikes"],
            extra["ebikes"],
            extra["slots"],
            int(extra["renting"]),
            int(extra["returning"]),
            extra["last_updated"],
        )


def test_fixture_covers_stockout_and_full_dock(conn, payload):
    store_snapshot(conn, payload, POLLED_AT)
    assert conn.execute("SELECT COUNT(*) FROM observations WHERE free_bikes = 0").fetchone()[0] >= 1
    assert conn.execute("SELECT COUNT(*) FROM observations WHERE empty_slots = 0").fetchone()[0] >= 1


def test_second_poll_appends_without_duplicating_stations(conn, payload):
    store_snapshot(conn, payload, POLLED_AT)
    store_snapshot(conn, payload, "2026-10-05T00:55:30+00:00")
    assert conn.execute("SELECT COUNT(*) FROM polls").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM stations").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 6


@pytest.mark.parametrize("raw, expected", [
    ("2026-10-05T00:47:29.821313+00:00Z", "2026-10-05T00:47:29.821313+00:00"),  # the API's real format
    ("2026-10-05T00:47:29Z", "2026-10-05T00:47:29+00:00"),
    ("2026-10-04T20:47:29-04:00", "2026-10-05T00:47:29+00:00"),  # converted to UTC
    ("not a time", "not a time"),  # kept as-is rather than lost
    (None, None),
])
def test_normalize_source_timestamp(raw, expected):
    assert normalize_source_timestamp(raw) == expected


def flaky_fetch(failures, payload, exc=requests.ConnectionError("network down")):
    """A fake fetch that raises `exc` for the first `failures` calls, then returns payload."""
    calls = []

    def fetch():
        calls.append(1)
        if len(calls) <= failures:
            raise exc
        return payload

    fetch.calls = calls
    return fetch


def test_failed_fetch_is_logged_as_a_gap(conn):
    fetch = flaky_fetch(failures=99, payload=None)
    waits = []

    poll_id, ok = poll_once(conn, fetch=fetch, sleep=waits.append)

    assert ok is False
    assert len(fetch.calls) == 3 and waits == [15, 30]  # retried, then gave up
    row = conn.execute("SELECT ok, n_stations, error_text FROM polls WHERE poll_id = ?", (poll_id,)).fetchone()
    assert row[0] == 0 and row[1] is None
    assert "network down" in row[2] and "3 attempts" in row[2]
    # One run leaves exactly one polls row, not one per attempt.
    assert conn.execute("SELECT COUNT(*) FROM polls").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0


def test_connection_error_then_success_stores_the_snapshot(conn, payload):
    """The case seen in practice: the laptop wakes and Wi-Fi returns a few seconds later."""
    fetch = flaky_fetch(failures=1, payload=payload)
    waits = []

    poll_id, ok = poll_once(conn, fetch=fetch, sleep=waits.append)

    assert ok is True
    assert len(fetch.calls) == 2 and waits == [15]
    assert conn.execute("SELECT ok FROM polls").fetchall() == [(1,)]
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 3


@pytest.mark.parametrize("exc", [
    requests.HTTPError("500 Server Error"),
    requests.ReadTimeout("server too slow"),  # the request reached the server
])
def test_errors_from_the_server_are_not_retried(conn, payload, exc):
    fetch = flaky_fetch(failures=1, payload=payload, exc=exc)
    waits = []

    poll_id, ok = poll_once(conn, fetch=fetch, sleep=waits.append)

    assert ok is False
    assert len(fetch.calls) == 1 and waits == []


def test_malformed_payload_leaves_no_partial_rows(conn, payload):
    payload["network"]["stations"][2].pop("id")  # third station breaks mid-write

    poll_id, ok = poll_once(conn, fetch=lambda: payload)

    assert ok is False
    # Only the failure row remains; the half-written poll was rolled back.
    assert conn.execute("SELECT ok FROM polls").fetchall() == [(0,)]
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0


def test_successful_poll_once(conn, payload):
    poll_id, ok = poll_once(conn, fetch=lambda: payload)
    assert ok is True
    assert conn.execute("SELECT ok FROM polls WHERE poll_id = ?", (poll_id,)).fetchone() == (1,)
