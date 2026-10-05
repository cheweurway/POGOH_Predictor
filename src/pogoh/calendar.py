"""Academic calendar flags for CMU and Pitt, from data/calendar.csv.

The CSV is hand-made from each school's official 2026-27 calendar. Each row
is an inclusive local-date range with one of three kinds:

- term: first day of classes through the end of the exam period
- no_classes: official no-class days inside the term (breaks, holidays)
- finals: the final exam period

calendar_flags() turns local dates into three 0/1 columns per school:
cmu_in_term, cmu_no_classes, cmu_finals, and the same for pitt.
"""

import pandas as pd

from pogoh.db import PROJECT_ROOT

DEFAULT_CALENDAR_PATH = PROJECT_ROOT / "data" / "calendar.csv"
KINDS = ("term", "no_classes", "finals")


def load_calendar(path=DEFAULT_CALENDAR_PATH):
    cal = pd.read_csv(path)
    cal["start_date"] = pd.to_datetime(cal["start_date"]).dt.date
    cal["end_date"] = pd.to_datetime(cal["end_date"]).dt.date
    unknown = set(cal["kind"]) - set(KINDS)
    if unknown:
        raise ValueError(f"unknown calendar kinds: {sorted(unknown)}")
    if (cal["end_date"] < cal["start_date"]).any():
        raise ValueError("calendar has a row that ends before it starts")
    return cal


def calendar_flags(local_dates, cal):
    """0/1 flags for each date. `local_dates` is a Series of datetime.date.

    Columns are <school>_in_term, <school>_no_classes, and <school>_finals,
    lowercased, for every school in the calendar. A date outside every range
    gets 0.
    """
    column_suffix = {"term": "in_term", "no_classes": "no_classes", "finals": "finals"}
    out = pd.DataFrame(index=local_dates.index)
    for school in sorted(cal["school"].unique()):
        for kind in KINDS:
            rows = cal[(cal["school"] == school) & (cal["kind"] == kind)]
            flag = pd.Series(False, index=local_dates.index)
            for start, end in zip(rows["start_date"], rows["end_date"]):
                flag |= (local_dates >= start) & (local_dates <= end)
            out[f"{school.lower()}_{column_suffix[kind]}"] = flag.astype("int8")
    return out
