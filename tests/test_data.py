"""Tests for loading observations into pandas."""

import json
from pathlib import Path

from pogoh.collect import record_failure, store_snapshot
from pogoh.data import load_observations, load_polls
from pogoh.db import connect

FIXTURE = Path(__file__).parent / "fixtures" / "snapshot_sample.json"


def test_loads_only_successful_polls_in_utc():
    conn = connect(":memory:")
    payload = json.loads(FIXTURE.read_text())
    store_snapshot(conn, payload, "2026-10-05T14:00:00+00:00")
    record_failure(conn, "2026-10-05T14:05:00+00:00", "ConnectionError: down")
    store_snapshot(conn, payload, "2026-10-05T14:10:00+00:00")

    obs = load_observations(conn)

    assert len(obs) == 6  # 3 stations x 2 successful polls
    assert str(obs["t"].dt.tz) == "UTC"
    assert obs["t"].min().isoformat() == "2026-10-05T14:00:00+00:00"
    # Sorted by station, then time.
    assert obs.groupby("station_id")["t"].apply(lambda s: s.is_monotonic_increasing).all()
    assert len(load_polls(conn)) == 3  # the failure is still visible here
