# Pogoh availability forecasting

Given a Pittsburgh Pogoh bike share station's recent history, will it have
zero free bikes (or zero empty docks) 30 and 60 minutes from now?

The data counts bikes at stations, not trips. A change in the count can come
from a rider or from a rebalancing truck, so results describe availability,
not demand.

Status: collecting data. Labels, features, and baselines are built and
tested (milestone 2). Models, results, and limitations come after 2 to 3
weeks of data.

## Data source

Station data comes from the [CityBikes API](https://citybik.es)
(`https://api.citybik.es/v2/networks/pittsburgh`), which is built on
[PyBikes](https://github.com/eskerda/pybikes). CityBikes is free to use and
asks projects to credit it as the source.

The API returns only a live snapshot, so this project polls it every 5 minutes
and keeps its own history.

Weather data by [Open-Meteo.com](https://open-meteo.com), from its
historical weather API, licensed CC BY 4.0.

The academic calendar in `data/calendar.csv` is hand-made from the official
CMU and University of Pittsburgh 2026-27 academic calendars.

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

## Labels

These decisions are fixed for the whole project.

- **Targets.** `stockout_30` and `stockout_60` are 1 when the station has 0
  free bikes 30 or 60 minutes after time t. `dock_full_30` is 1 when it has 0
  empty docks 30 minutes after t. Stockouts are the primary targets.
- **Matching the future.** Polls are irregular, so the state at t + H is the
  observation of the same station nearest to t + H, and only if it is within
  3 minutes. Otherwise the label is missing and the row is dropped for that
  target. Nothing is interpolated. Each target is matched separately, so a
  row can have a 30 minute label but no 60 minute one.
- **What counts as empty.** Only `free_bikes == 0`. A station that reports
  it is not renting, but still lists bikes, is not counted as a stockout.
  So far every station has always reported renting.
- **Transition rows.** A row is a transition when the current state differs
  from the future state, for example bikes now and none in 30 minutes.
  Results are reported overall and on transition rows, because predicting
  "no change" is hard to beat at stations that sit empty for hours.
- **No future data in features.** Features at time t use only observations
  at or before t. Tests rebuild features after deleting or scrambling every
  later row and fail if anything changes.
- **Split.** By time: oldest 70 percent of rows for training, next 15 percent
  for validation, newest 15 percent for a single final test. Rows within 60
  minutes before each boundary are dropped so that no training label is
  taken from validation time, and no validation label from test time.

## Features

Current free bikes, empty docks, e-bikes, capacity (free plus empty), and
fraction full. Values and changes from about 10, 30, and 60 minutes earlier
(missing if no observation is close enough, never filled in), and minutes
since the station's previous observation. Hour of day as sine and cosine,
day of week, and weekend, in Pittsburgh time. CMU and Pitt in-term,
no-classes, and finals flags. Hourly temperature and precipitation from the
latest hour at or before t.

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
- `src/pogoh/data.py`: loads observations and polls from the database into
  pandas, with times in UTC.
- `src/pogoh/labels.py`: the future-state targets and transition flags.
- `src/pogoh/features.py`: model inputs, built from past data only.
- `src/pogoh/calendar.py` and `data/calendar.csv`: academic calendar flags.
- `src/pogoh/weather.py`, `scripts/update_weather.py`: hourly weather,
  cached in `data/weather.csv` (not committed).
- `src/pogoh/evaluate.py`: the time split.
- `src/pogoh/baselines.py`: persistence and hour-of-week baselines.
- `tests/`: tests for every module above, including the leakage tests.

## Preparing data for analysis

```powershell
.venv\Scripts\python scripts\import_raw.py
.venv\Scripts\python scripts\update_weather.py
```

## Data notes

- The source timestamp arrives as `...+00:00Z` and is normalized to UTC on
  storage. `extra.last_updated` has no timezone and is stored exactly as
  received. Early polls suggest it is UTC (it trails our poll time by about
  a minute), to be confirmed before it is used.
- About half the stations report a total dock count (`slots`) larger than
  free bikes plus empty slots, likely docks out of service. Both are stored.
