"""Tests for reading records out of a git branch, using a throwaway repository."""

import importlib.util
import json
import subprocess
from pathlib import Path

from pogoh.db import connect
from pogoh.raw import import_records, make_record, write_record

FIXTURE = Path(__file__).parent / "fixtures" / "snapshot_sample.json"

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_raw.py"
_spec = importlib.util.spec_from_file_location("import_raw", _SCRIPT)
import_raw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(import_raw)


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _make_repo(tmp_path, records):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "-b", "data")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("data branch\n")
    for record in records:
        write_record(repo, record)
    _git(repo, "add", ".")
    _git(repo, "commit", "--quiet", "-m", "records")
    return repo


def test_reads_all_records_from_the_branch(tmp_path):
    payload = json.loads(FIXTURE.read_text())
    records = [
        make_record("2026-10-05T14:00:00+00:00", payload=payload),
        make_record("2026-10-05T14:05:00+00:00", error_text="ConnectionError: down"),
    ]
    repo = _make_repo(tmp_path, records)

    found = list(import_raw.read_records_from_branch("data", repo=repo))

    assert sorted(found, key=lambda r: r["polled_at_utc"]) == records
    conn = connect(":memory:")
    assert import_records(conn, found) == (2, 0)


def test_branch_without_records_yields_nothing(tmp_path):
    repo = _make_repo(tmp_path, [])
    assert list(import_raw.read_records_from_branch("data", repo=repo)) == []
