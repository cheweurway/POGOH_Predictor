# Pogoh raw poll data

This branch holds data only. The code lives on `main`.

A GitHub Actions workflow (`.github/workflows/collect.yml` on `main`) polls
the CityBikes API for Pittsburgh's Pogoh bike share every 5 minutes and adds
one gzipped JSON file per poll under `raw/YYYY/MM/DD/`. Failed polls are
saved too, with `ok` set to false and the error text.

Each file holds `polled_at_utc`, `ok`, `error_text`, and `payload` (the API
response exactly as received). Files are only ever added, never changed.

Data source: [CityBikes](https://citybik.es), built on
[PyBikes](https://github.com/eskerda/pybikes).
