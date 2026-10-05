"""Database schema and connection helpers.

The database is a single SQLite file (data/pogoh.db by default) with three
tables:

- polls: one row per attempt to fetch the API, successful or not. Failed
  polls are kept so gaps in the data are visible later.
- stations: one row per station, recorded the first time we see it.
- observations: one row per station per successful poll.

Collected data is append only. Triggers below make SQLite refuse any UPDATE
or DELETE on these tables, so a bug cannot silently rewrite history.

All times we generate are stored as ISO 8601 strings in UTC.
"""

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "pogoh.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS polls (
    poll_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    polled_at_utc  TEXT    NOT NULL,   -- when we made the request
    ok             INTEGER NOT NULL,   -- 1 if stored, 0 if the poll failed
    n_stations     INTEGER,            -- stations in the response, NULL on failure
    error_text     TEXT                -- why it failed, NULL on success
);

CREATE TABLE IF NOT EXISTS stations (
    station_id  TEXT PRIMARY KEY,
    name        TEXT,
    latitude    REAL,
    longitude   REAL
);

CREATE TABLE IF NOT EXISTS observations (
    poll_id              INTEGER NOT NULL REFERENCES polls(poll_id),
    station_id           TEXT    NOT NULL REFERENCES stations(station_id),
    free_bikes           INTEGER,
    empty_slots          INTEGER,
    normal_bikes         INTEGER,
    ebikes               INTEGER,
    slots                INTEGER,  -- total docks reported by the source
    is_renting           INTEGER,  -- 1 if the station allows rentals
    is_returning         INTEGER,  -- 1 if the station accepts returns
    source_timestamp     TEXT,     -- the source's timestamp, normalized to UTC
    source_last_updated  TEXT,     -- extra.last_updated exactly as received
    PRIMARY KEY (poll_id, station_id)
);

CREATE INDEX IF NOT EXISTS idx_obs_station ON observations(station_id, poll_id);
"""

# One pair of triggers per table: block UPDATE and DELETE.
APPEND_ONLY_TRIGGERS = "\n".join(
    f"""
CREATE TRIGGER IF NOT EXISTS {table}_no_{action}
BEFORE {action.upper()} ON {table}
BEGIN
    SELECT RAISE(ABORT, '{table} is append only');
END;
"""
    for table in ("polls", "stations", "observations")
    for action in ("update", "delete")
)


def connect(db_path=DEFAULT_DB_PATH):
    """Open the database, creating the file, folder, and tables if needed.

    Pass ":memory:" for a throwaway database (used by the tests).
    """
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


def init_schema(conn):
    """Create tables, indexes, and append-only triggers if they are missing."""
    conn.executescript(SCHEMA + APPEND_ONLY_TRIGGERS)
    conn.commit()
