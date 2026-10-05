"""Tests for the schema and the append-only rule."""

import sqlite3

import pytest

from pogoh.db import connect


def test_tables_are_created():
    conn = connect(":memory:")
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"polls", "stations", "observations"} <= names


def test_connect_twice_is_safe(tmp_path):
    path = tmp_path / "pogoh.db"
    connect(path).close()
    connect(path).close()  # schema creation must not fail on an existing file


@pytest.mark.parametrize("sql", [
    "UPDATE polls SET ok = 0",
    "DELETE FROM polls",
])
def test_polls_cannot_be_changed(sql):
    conn = connect(":memory:")
    conn.execute("INSERT INTO polls (polled_at_utc, ok) VALUES ('2026-10-05T00:00:00+00:00', 1)")
    with pytest.raises(sqlite3.IntegrityError, match="append only"):
        conn.execute(sql)
