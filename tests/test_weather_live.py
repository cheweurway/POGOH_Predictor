"""Tests for saving live weather records (the cloud side). No network calls."""

import importlib.util
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from pogoh.raw import decode_record, latest_success_time, read_records_from_branch
from pogoh.weather_live import FOLDER, TIME_KEY, fetch_record, weather_relpath

LIVE = Path(__file__).parent / "fixtures" / "weather_live_sample.json"
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "weather_to_file.py"
_spec = importlib.util.spec_from_file_location("weather_to_file", _SCRIPT)
weather_to_file = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(weather_to_file)

NOW = datetime(2026, 11, 12, 20, 15, 2, 123456, tzinfo=timezone.utc)


def payload():
    return json.loads(LIVE.read_text(encoding="utf-8"))


def files(root):
    return sorted(Path(root).glob(f"{FOLDER}/*/*/*/*.json.gz"))


def test_relpath_is_named_by_fetch_time():
    assert weather_relpath(NOW.isoformat()) == Path("weather/2026/11/12/20261112T201502_123456Z.json.gz")


def test_fetch_record_turns_errors_into_failed_records():
    def down():
        raise requests.ConnectionError("no route")

    record = fetch_record(down, now=NOW)
    assert record["ok"] is False and record["payload"] is None
    assert record["error_text"].startswith("ConnectionError")
    assert record[TIME_KEY] == NOW.isoformat()


def test_script_writes_a_record(tmp_path):
    assert weather_to_file.main(["--out", str(tmp_path)], fetch=payload, now=NOW) == 0
    [path] = files(tmp_path)
    record = decode_record(path.read_bytes())
    assert record["ok"] and record["payload"] == payload()
    assert latest_success_time(tmp_path, folder=FOLDER, time_key=TIME_KEY) == NOW


def test_script_skips_within_14_minutes_but_not_after(tmp_path):
    weather_to_file.main(["--out", str(tmp_path)], fetch=payload, now=NOW)
    weather_to_file.main(["--out", str(tmp_path)], fetch=payload, now=NOW + timedelta(minutes=10))
    assert len(files(tmp_path)) == 1
    weather_to_file.main(["--out", str(tmp_path)], fetch=payload, now=NOW + timedelta(minutes=15))
    assert len(files(tmp_path)) == 2


def test_failed_fetch_is_saved_and_does_not_block_the_next_run(tmp_path):
    def down():
        raise requests.Timeout("slow")

    assert weather_to_file.main(["--out", str(tmp_path)], fetch=down, now=NOW) == 0
    assert weather_to_file.main(["--out", str(tmp_path)], fetch=payload,
                                now=NOW + timedelta(minutes=5)) == 0
    oks = [decode_record(p.read_bytes())["ok"] for p in files(tmp_path)]
    assert oks == [False, True]


def test_weather_does_not_affect_the_poll_guard(tmp_path):
    weather_to_file.main(["--out", str(tmp_path)], fetch=payload, now=NOW)
    assert latest_success_time(tmp_path) is None  # raw/ is still empty


def test_records_are_read_back_from_the_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    git("init", "--quiet", "-b", "data")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.com")
    weather_to_file.main(["--out", str(repo)], fetch=payload, now=NOW)
    git("add", "-A")
    git("commit", "--quiet", "-m", "weather")

    [record] = read_records_from_branch("data", repo=repo, folder=FOLDER)
    assert record[TIME_KEY] == NOW.isoformat()
    assert list(read_records_from_branch("data", repo=repo)) == []  # no raw/ polls
