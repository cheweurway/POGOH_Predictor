"""Score the two baselines on the validation split and count what the data holds.

    .venv\\Scripts\\python scripts\\run_baselines.py

Prints, for each target:
  - how many labeled rows, positives, and transition rows each split has,
    and transitions per day (to judge whether there is enough data yet)
  - persistence and hour-of-week scores on VALIDATION rows only

The test split is counted but never scored here. It is used once, at the
end of the project.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pogoh.baselines import HourOfWeekRate, persistence  # noqa: E402
from pogoh.data import load_observations  # noqa: E402
from pogoh.db import DEFAULT_DB_PATH  # noqa: E402
from pogoh.evaluate import format_metrics, score, time_split  # noqa: E402
from pogoh.labels import TARGETS, add_labels  # noqa: E402

SPLITS = ("train", "validation", "test")


def main():
    obs = load_observations(sqlite3.connect(DEFAULT_DB_PATH))
    if obs.empty:
        print("No observations yet.")
        return 1
    df = add_labels(obs)
    df["split"] = time_split(df)

    print(f"{len(df)} observations, {df['station_id'].nunique()} stations, "
          f"{df['t'].min():%Y-%m-%d %H:%M} to {df['t'].max():%Y-%m-%d %H:%M} UTC")
    for name in SPLITS:
        t = df.loc[df["split"] == name, "t"]
        print(f"  {name:<10} {t.min():%m-%d %H:%M} to {t.max():%m-%d %H:%M} UTC, {len(t)} rows")

    for target in TARGETS:
        print(f"\n=== {target} ===")
        print(f"  {'split':<10} {'labeled':>8} {'positive':>9} {'transitions':>12} {'per day':>8}")
        for name in SPLITS:
            rows = df[(df["split"] == name) & df[target].notna()]
            days = (rows["t"].max() - rows["t"].min()).total_seconds() / 86400
            n_tr = int((rows[f"{target}_transition"] == 1).sum())
            per_day = f"{n_tr / days:8.1f}" if days > 0 else "     n/a"
            print(f"  {name:<10} {len(rows):>8} {int(rows[target].sum()):>9} {n_tr:>12} {per_day}")

        train = df[(df["split"] == "train") & df[target].notna()]
        val = df[(df["split"] == "validation") & df[target].notna()]
        predictions = {
            "persistence": persistence(val, target),
            "hour of week": HourOfWeekRate(target).fit(train).predict_proba(val),
        }
        print("  Validation scores (warning when score >= 0.5):")
        for label, p in predictions.items():
            result = score(val[target], p, val[f"{target}_transition"])
            print(f"    {label:<13} all         {format_metrics(result['all'])}")
            print(f"    {'':<13} transitions {format_metrics(result['transition'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
