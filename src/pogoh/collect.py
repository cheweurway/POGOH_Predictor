"""Fetch one snapshot of the Pogoh network and store it.

The pieces are kept separate so each can be tested on its own:

- fetch_snapshot() does the HTTP request and nothing else.
- store_snapshot() writes an already-fetched payload to the database. It never
  touches the network, so tests can feed it a saved JSON file.
- record_failure() logs a poll that did not produce data.
- poll_once() ties them together and makes sure every attempt leaves exactly
  one row in the polls table, whether it worked or not.

Data source: CityBikes (https://citybik.es), which is free to use with
attribution.
"""

from datetime import datetime, timezone

import requests

API_URL = "https://api.citybik.es/v2/networks/pittsburgh"
REQUEST_TIMEOUT_SECONDS = 30


def utc_now_iso():
    """Current time in UTC as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def fetch_snapshot(url=API_URL, timeout=REQUEST_TIMEOUT_SECONDS):
    """Request the network snapshot and return the parsed JSON.

    Raises an exception on any network error, bad HTTP status, or a response
    that does not look like a station list, so the caller can log a failure.
    """
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    stations = payload.get("network", {}).get("stations")
    if not isinstance(stations, list):
        raise ValueError("response has no network.stations list")
    return payload


def normalize_source_timestamp(raw):
    """Turn the source's timestamp into a clean UTC ISO string.

    The API sends values like '2026-10-05T00:47:29.821313+00:00Z', which has
    both an offset and a trailing 'Z', so standard parsers reject it. We drop
    the redundant 'Z' and convert to UTC. If parsing still fails we return the
    raw string unchanged rather than lose the value.
    """
    if raw is None:
        return None
    text = raw
    if text.endswith("Z"):
        # A plain 'Z' means UTC. A 'Z' after an explicit offset is redundant.
        has_offset = "+" in text[10:] or "-" in text[10:]
        text = text[:-1] if has_offset else text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        return raw
    return parsed.astimezone(timezone.utc).isoformat()


def _as_int_flag(value):
    """Store booleans (or 0/1 values) as 1, 0, or NULL."""
    if value is None:
        return None
    return int(bool(value))


def store_snapshot(conn, payload, polled_at_utc):
    """Write one successful poll and its observations. Returns the poll_id.

    Everything happens in one transaction: either the poll and all of its
    station rows are saved, or nothing is.
    """
    stations = payload["network"]["stations"]
    with conn:  # commits on success, rolls back on any exception
        cursor = conn.execute(
            "INSERT INTO polls (polled_at_utc, ok, n_stations, error_text) VALUES (?, 1, ?, NULL)",
            (polled_at_utc, len(stations)),
        )
        poll_id = cursor.lastrowid
        for station in stations:
            extra = station.get("extra") or {}
            # Keep the first name and location we see. Later changes are
            # ignored here; they can be detected later if they ever matter.
            conn.execute(
                "INSERT OR IGNORE INTO stations (station_id, name, latitude, longitude) VALUES (?, ?, ?, ?)",
                (station["id"], station.get("name"), station.get("latitude"), station.get("longitude")),
            )
            conn.execute(
                """
                INSERT INTO observations (
                    poll_id, station_id, free_bikes, empty_slots, normal_bikes, ebikes,
                    slots, is_renting, is_returning, source_timestamp, source_last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    poll_id,
                    station["id"],
                    station.get("free_bikes"),
                    station.get("empty_slots"),
                    extra.get("normal_bikes"),
                    extra.get("ebikes"),
                    extra.get("slots"),
                    _as_int_flag(extra.get("renting")),
                    _as_int_flag(extra.get("returning")),
                    normalize_source_timestamp(station.get("timestamp")),
                    None if extra.get("last_updated") is None else str(extra["last_updated"]),
                ),
            )
    return poll_id


def record_failure(conn, polled_at_utc, error_text):
    """Log a poll that produced no data. Returns the poll_id."""
    with conn:
        cursor = conn.execute(
            "INSERT INTO polls (polled_at_utc, ok, n_stations, error_text) VALUES (?, 0, NULL, ?)",
            (polled_at_utc, error_text[:1000]),
        )
    return cursor.lastrowid


def poll_once(conn, fetch=fetch_snapshot):
    """Fetch and store one snapshot. Returns (poll_id, ok).

    `fetch` can be swapped out in tests to simulate success or failure
    without calling the real API.
    """
    polled_at = utc_now_iso()
    try:
        payload = fetch()
        return store_snapshot(conn, payload, polled_at), True
    except Exception as exc:  # any failure becomes a logged gap, not a crash
        return record_failure(conn, polled_at, f"{type(exc).__name__}: {exc}"), False
