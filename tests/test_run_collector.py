"""Tests for the minimum-gap guard in scripts/run_collector.py."""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pogoh.collect import record_failure
from pogoh.db import connect

# scripts/ is not a package, so load the script as a module by path.
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_collector.py"
_spec = importlib.util.spec_from_file_location("run_collector", _SCRIPT)
run_collector = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_collector)


def _ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _record_success(conn, polled_at):
    with conn:
        conn.execute("INSERT INTO polls (polled_at_utc, ok, n_stations) VALUES (?, 1, 0)", (polled_at,))


def test_no_polls_means_no_gap():
    assert run_collector.seconds_since_last_poll(connect(":memory:")) is None


def test_recent_success_counts():
    conn = connect(":memory:")
    _record_success(conn, _ago(60))
    assert 55 < run_collector.seconds_since_last_poll(conn) < 65


def test_recent_failure_does_not_block_the_next_run():
    """The case seen in practice: a failure on wake, then a scheduled run 40s later."""
    conn = connect(":memory:")
    _record_success(conn, _ago(600))
    record_failure(conn, _ago(40), "ConnectionError: wifi not back yet")

    gap = run_collector.seconds_since_last_poll(conn)

    assert gap > run_collector.MIN_GAP_SECONDS  # measured from the success, so the run proceeds
