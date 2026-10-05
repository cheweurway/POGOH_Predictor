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
and stores the history locally in `data/pogoh.db` (not committed).

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m pytest
```

Run one poll by hand:

```powershell
.venv\Scripts\python scripts\run_collector.py
```

Schedule it every 5 minutes with Windows Task Scheduler:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1
```

## Files

- `src/pogoh/db.py`: SQLite schema (polls, stations, observations) and the
  connection helper. Triggers make the tables append only.
- `src/pogoh/collect.py`: fetches one snapshot, stores it in a single
  transaction, and logs failed polls so gaps stay visible.
- `scripts/run_collector.py`: runs one poll, skips if the last poll was under
  4 minutes ago, and writes a line to `data/collector.log`.
- `scripts/install_schedule.ps1`: registers the scheduled task.
- `tests/`: tests for the schema and for storing a saved snapshot.

## Data notes

- The source timestamp arrives as `...+00:00Z` and is normalized to UTC on
  storage. `extra.last_updated` has no timezone and is stored exactly as
  received. Early polls suggest it is UTC (it trails our poll time by about
  a minute), to be confirmed before it is used.
- About half the stations report a total dock count (`slots`) larger than
  free bikes plus empty slots, likely docks out of service. Both are stored.
