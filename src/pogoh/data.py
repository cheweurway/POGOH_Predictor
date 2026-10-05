"""Load collected observations from the database into a pandas table.

This is the starting point for labels, features, and models. It returns one
row per station per successful poll:

    station_id, t, poll_id, free_bikes, empty_slots, normal_bikes, ebikes,
    slots, is_renting, is_returning, source_last_updated

`t` is our poll time as a timezone-aware UTC timestamp. Failed polls have no
observations, so they simply leave a gap in time; they stay in the polls
table for gap analysis (see load_polls).
"""

import pandas as pd

OBSERVATIONS_SQL = """
SELECT o.station_id, p.polled_at_utc AS t, o.poll_id,
       o.free_bikes, o.empty_slots, o.normal_bikes, o.ebikes, o.slots,
       o.is_renting, o.is_returning, o.source_last_updated
FROM observations o
JOIN polls p ON p.poll_id = o.poll_id
WHERE p.ok = 1
"""


def to_utc(series):
    """Parse ISO 8601 strings into UTC timestamps."""
    return pd.to_datetime(series, utc=True, format="ISO8601")


def load_observations(conn):
    """All observations from successful polls, sorted by station and time."""
    df = pd.read_sql_query(OBSERVATIONS_SQL, conn)
    df["t"] = to_utc(df["t"])
    return df.sort_values(["station_id", "t"], ignore_index=True)


def load_polls(conn):
    """Every poll attempt, including failures, sorted by time."""
    df = pd.read_sql_query("SELECT * FROM polls", conn)
    df["polled_at_utc"] = to_utc(df["polled_at_utc"])
    return df.sort_values("polled_at_utc", ignore_index=True)
