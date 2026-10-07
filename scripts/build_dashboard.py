"""Build the dashboard: JSON data files plus the static page, in one folder.

Two data sources:

    # On the laptop, from the local database (run import_raw.py first):
    .venv\\Scripts\\python scripts\\build_dashboard.py --db data\\pogoh.db --out data\\dashboard_site

    # In GitHub Actions, from the fetched data branch (no database needed):
    python scripts/build_dashboard.py --from-branch --out _site

--from-branch reads refs/remotes/origin/data, so run `git fetch origin data`
first. It loads every raw record into a throwaway in-memory database and
never writes to the branch.

Output layout (everything the browser needs, nothing else):

    <out>/index.html, style.css, app.js   copied from dashboard/ if present; the
                                          page links to style.css and app.js with
                                          ?v=<content hash> so browsers never use
                                          a stale copy after an update
    <out>/data/meta.json                  counts, timestamps, credits
    <out>/data/stations.json              newest state of every station
    <out>/data/empty_full_24h.json        share of the last 24 hours each station was empty or full
    <out>/data/heatmap.json               stockout share by station and hour of day
    <out>/data/health.json                polls per hour and the gap list
    <out>/data/series/<station_id>.json   one station's history and rebalancing events

The <out>/data folder is deleted and rebuilt on every run so no stale files
survive. Nothing outside <out> is touched.
"""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from pogoh import dashboard  # noqa: E402
from pogoh.data import load_observations, load_polls  # noqa: E402
from pogoh.db import connect  # noqa: E402
from pogoh.raw import DATA_REF, import_records, read_records_from_branch  # noqa: E402

DEFAULT_SITE_DIR = PROJECT_ROOT / "dashboard"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    # allow_nan=False: NaN is not valid JSON and would break the browser.
    path.write_text(json.dumps(value, separators=(",", ":"), allow_nan=False), encoding="utf-8")


def add_version_tags(html, out):
    """Point the page's links to style.css and app.js at ?v=<content hash>.

    Browsers and GitHub Pages cache these files, so after an update a
    visitor could get the new page with the old style or script. A link
    that changes whenever the file changes forces a fresh download.
    """
    for name, attr in (("style.css", "href"), ("app.js", "src")):
        path = Path(out) / name
        if path.exists():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
            html = html.replace(f'{attr}="{name}"', f'{attr}="{name}?v={digest}"')
    return html


def open_source(args):
    """Return a database connection holding the data to publish."""
    if args.from_branch:
        conn = connect(":memory:")
        import_records(conn, read_records_from_branch(args.ref))
        return conn
    if not Path(args.db).exists():
        raise SystemExit(f"Database not found: {args.db}. Run scripts\\import_raw.py first.")
    return connect(args.db)


def build(conn, out, site_dir=DEFAULT_SITE_DIR, built_at=None):
    """Write all dashboard files into `out`. Returns a short summary dict."""
    out = Path(out)
    built_at = built_at or pd.Timestamp(datetime.now(timezone.utc))
    obs = load_observations(conn)
    polls = load_polls(conn)
    stations = pd.read_sql_query("SELECT station_id, name, latitude, longitude FROM stations", conn)
    names = dict(zip(stations["station_id"], stations["name"]))

    data_dir = out / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)

    write_json(data_dir / "meta.json", dashboard.meta(polls, obs, built_at))
    write_json(data_dir / "stations.json", dashboard.latest_snapshot(obs, stations))

    shares = dashboard.empty_full_share(obs)
    places = stations.set_index("station_id")
    for row in shares["stations"]:
        row["name"] = names.get(row["station_id"])
        row["lat"] = dashboard._clean(places.at[row["station_id"], "latitude"])
        row["lon"] = dashboard._clean(places.at[row["station_id"], "longitude"])
    write_json(data_dir / "empty_full_24h.json", shares)

    heatmap = dashboard.stockout_heatmap(obs)
    heatmap["names"] = [names.get(s) for s in heatmap["station_ids"]]
    write_json(data_dir / "heatmap.json", heatmap)

    write_json(data_dir / "health.json", dashboard.poll_health(polls))

    events_by_station = {}
    for event in dashboard.rebalancing_events(obs):
        events_by_station.setdefault(event["station_id"], []).append(event)
    series = dashboard.station_series(obs)
    for station_id, s in series.items():
        s["station_id"] = station_id
        s["name"] = names.get(station_id)
        s["rebalancing"] = events_by_station.get(station_id, [])
        write_json(data_dir / "series" / f"{station_id}.json", s)

    copied = []
    site_dir = Path(site_dir)
    if site_dir.is_dir():
        for path in site_dir.iterdir():
            if path.is_file():
                shutil.copy2(path, out / path.name)
                copied.append(path.name)
    index = out / "index.html"
    if index.exists():
        index.write_text(add_version_tags(index.read_text(encoding="utf-8"), out), encoding="utf-8")

    return {"stations": len(series), "polls": len(polls), "observations": len(obs), "static_files": copied}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the dashboard data and page")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--db", help="local SQLite database, for example data\\pogoh.db")
    source.add_argument("--from-branch", action="store_true", help="read the fetched data branch")
    parser.add_argument("--ref", default=DATA_REF, help="git ref to read with --from-branch")
    parser.add_argument("--out", required=True, help="output folder, for example _site")
    parser.add_argument("--site-dir", default=DEFAULT_SITE_DIR, help="folder with index.html, style.css, app.js")
    args = parser.parse_args(argv)

    conn = open_source(args)
    try:
        summary = build(conn, args.out, args.site_dir)
    finally:
        conn.close()
    print(f"Built dashboard in {args.out}: {summary['stations']} stations, {summary['polls']} polls, "
          f"{summary['observations']} observations, static files: {summary['static_files'] or 'none yet'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
