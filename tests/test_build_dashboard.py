"""Smoke tests for scripts/build_dashboard.py, using the saved API fixture."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from pogoh.collect import record_failure, store_snapshot
from pogoh.db import connect
from pogoh.raw import import_records, make_record, read_records_from_branch, write_record

FIXTURE = Path(__file__).parent / "fixtures" / "snapshot_sample.json"  # 3 stations

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_dashboard.py"
_spec = importlib.util.spec_from_file_location("build_dashboard", _SCRIPT)
build_dashboard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_dashboard)

POLL_TIMES = ["2026-10-05T14:00:00+00:00", "2026-10-05T14:05:00+00:00"]


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def db_path(tmp_path, payload):
    path = tmp_path / "pogoh.db"
    conn = connect(path)
    for t in POLL_TIMES:
        store_snapshot(conn, payload, t)
    record_failure(conn, "2026-10-05T14:10:00+00:00", "ConnectionError: down")
    conn.close()
    return path


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_build_from_database_writes_every_file(tmp_path, db_path):
    out = tmp_path / "site"
    assert build_dashboard.main(["--db", str(db_path), "--out", str(out), "--site-dir", str(tmp_path / "none")]) == 0

    data = out / "data"
    meta = _load(data / "meta.json")
    assert meta["n_polls_ok"] == 2 and meta["n_polls_failed"] == 1 and meta["n_stations"] == 3
    assert "CityBikes" in meta["credits"]

    stations = _load(data / "stations.json")
    assert len(stations) == 3 and all(s["lat"] and s["lon"] for s in stations)

    system = _load(data / "system.json")
    assert system["n_stations"] == [3, 3]

    shares = _load(data / "empty_full_24h.json")
    assert len(shares["stations"]) == 3
    assert all(r["name"] and r["lat"] and r["lon"] for r in shares["stations"])
    assert shares["window_hours"] == 24

    heatmap = _load(data / "heatmap.json")
    assert len(heatmap["names"]) == 3 and None not in heatmap["names"]

    health = _load(data / "health.json")
    assert sum(health["failed"]) == 1

    series_files = list((data / "series").glob("*.json"))
    assert len(series_files) == 3
    one = _load(series_files[0])
    assert one["name"] and len(one["free_bikes"]) == 2 and one["rebalancing"] == []
    assert len(one["typical_day"]["mean"]) == 24


def test_rebuild_removes_stale_files(tmp_path, db_path):
    out = tmp_path / "site"
    stale = out / "data" / "series" / "old_station.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}")
    build_dashboard.main(["--db", str(db_path), "--out", str(out), "--site-dir", str(tmp_path / "none")])
    assert not stale.exists()


def test_static_files_are_copied(tmp_path, db_path):
    site = tmp_path / "dashboard"
    site.mkdir()
    (site / "index.html").write_text("<!doctype html><title>t</title>")
    out = tmp_path / "site"
    build_dashboard.main(["--db", str(db_path), "--out", str(out), "--site-dir", str(site)])
    assert (out / "index.html").read_text() == "<!doctype html><title>t</title>"


def test_page_links_carry_a_content_hash(tmp_path, db_path):
    site = tmp_path / "dashboard"
    site.mkdir()
    (site / "index.html").write_text('<link href="style.css"><script src="app.js"></script>')
    (site / "style.css").write_text("body {}")
    (site / "app.js").write_text("1;")
    out = tmp_path / "site"
    build_dashboard.main(["--db", str(db_path), "--out", str(out), "--site-dir", str(site)])
    first = (out / "index.html").read_text()
    assert 'href="style.css?v=' in first and 'src="app.js?v=' in first

    (site / "style.css").write_text("body { color: red }")
    build_dashboard.main(["--db", str(db_path), "--out", str(out), "--site-dir", str(site)])
    second = (out / "index.html").read_text()
    assert second != first  # a changed stylesheet gets a new link
    assert second.split("app.js")[1] == first.split("app.js")[1]  # unchanged script keeps its link


def test_missing_database_gives_a_clear_error(tmp_path):
    with pytest.raises(SystemExit, match="import_raw"):
        build_dashboard.main(["--db", str(tmp_path / "nope.db"), "--out", str(tmp_path / "site")])


def test_build_from_branch_matches_build_from_database(tmp_path, payload):
    # A throwaway git repo whose `data` branch holds the same polls as raw files.
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "--quiet", "-b", "data"], ["config", "user.name", "t"], ["config", "user.email", "t@example.com"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    for t in POLL_TIMES:
        write_record(repo, make_record(t, payload=payload))
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "--quiet", "-m", "polls"], check=True, capture_output=True)

    conn = connect(":memory:")
    import_records(conn, read_records_from_branch("data", repo=repo))
    summary = build_dashboard.build(conn, tmp_path / "site", site_dir=tmp_path / "none")
    assert summary["stations"] == 3 and summary["polls"] == 2
