# Pogoh availability forecasting

Given a Pittsburgh Pogoh bike share station's recent history, will it have
zero free bikes (or zero empty docks) 30 and 60 minutes from now?

The data counts bikes at stations, not trips. A change in the count can come
from a rider or from a rebalancing truck, so results describe availability,
not demand.

Status: data collection (milestone 1). Method, results, and limitations will
be filled in as the project progresses.

## Data source

Station data comes from the [CityBikes API](https://citybik.es)
(`https://api.citybik.es/v2/networks/pittsburgh`), which is built on
[PyBikes](https://github.com/eskerda/pybikes). CityBikes is free to use and
asks projects to credit it as the source.

The API returns only a live snapshot, so this project polls it every 5 minutes
and keeps its own history.

## How data is collected

The main collector is a scheduled GitHub Actions workflow
(`.github/workflows/collect.yml`). Every 5 minutes it polls the API and
commits one gzipped JSON file per poll to the `data` branch of this
repository, so collection does not depend on a laptop being awake. The raw
data is therefore public, like the API it comes from.

GitHub runs scheduled workflows on a best-effort basis: runs can be delayed
or dropped when GitHub is busy. Every record stores the actual fetch time,
so the irregular spacing is visible and handled when building labels.

For analysis, the laptop imports the branch into a local SQLite database,
`data/pogoh.db`, which is not committed:

```powershell
.venv\Scripts\python scripts\import_raw.py
```

A Windows Task Scheduler collector that writes straight to the local
database is also available (see below). It was the first setup, and gaps
from laptop sleep made it unreliable on its own.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m pytest
```

Optional local collector, one poll by hand or every 5 minutes on a schedule:

```powershell
.venv\Scripts\python scripts\run_collector.py
powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1
```

## Files

- `src/pogoh/db.py`: SQLite schema (polls, stations, observations) and the
  connection helper. Triggers make the tables append only.
- `src/pogoh/collect.py`: fetches one snapshot with retries, stores it in a
  single transaction, and logs failed polls so gaps stay visible.
- `src/pogoh/raw.py`: the one-file-per-poll record format used on the `data`
  branch, and the import of those records into the database.
- `.github/workflows/collect.yml`: the scheduled cloud collector.
- `scripts/poll_to_file.py`: the poll step the workflow runs.
- `scripts/import_raw.py`: fetches the `data` branch and imports new polls.
- `scripts/run_collector.py`, `scripts/install_schedule.ps1`: the optional
  local collector and its Task Scheduler setup.
- `tests/`: tests for the schema, collection, raw records, and import.

## Data notes

- The source timestamp arrives as `...+00:00Z` and is normalized to UTC on
  storage. `extra.last_updated` has no timezone and is stored exactly as
  received. Early polls suggest it is UTC (it trails our poll time by about
  a minute), to be confirmed before it is used.
- About half the stations report a total dock count (`slots`) larger than
  free bikes plus empty slots, likely docks out of service. Both are stored.
