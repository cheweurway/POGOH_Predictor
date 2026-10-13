"""Splitting data by time, and scoring predictions.

time_split() puts the oldest 70 percent of rows in train, the next 15
percent in validation, and the newest 15 percent in test. Rows are never
shuffled across time.

A gap (default 60 minutes, the longest label horizon) is left before each
boundary. Without it, a training row at 11:50 with a 60 minute label would
be labeled with data from 12:50, inside the validation period. Rows that
fall in a gap are dropped.

score() turns predictions into the metrics this project reports: precision,
recall, F1, and PR-AUC, plus accuracy only next to the base rate (stockouts
are rare, so "never empty" already gets high accuracy). Each is reported
on all rows and again on transition rows, where the state now differs from
the state later. Persistence is always wrong there by construction, so that
is where a model has to show it adds something.
"""

import math

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

SPLIT_FRACTIONS = (0.70, 0.15, 0.15)
SPLIT_GAP = pd.Timedelta(minutes=60)
THRESHOLD = 0.5


def time_split(df, fractions=SPLIT_FRACTIONS, gap=SPLIT_GAP):
    """Return a Series of 'train', 'validation', 'test', or NA (gap), aligned to df."""
    if abs(sum(fractions) - 1) > 1e-9:
        raise ValueError("split fractions must add up to 1")
    t = df["t"]
    val_start = t.quantile(fractions[0])
    test_start = t.quantile(fractions[0] + fractions[1])

    split = pd.Series(pd.NA, index=df.index, dtype="string")
    split[t < val_start - gap] = "train"
    split[(t >= val_start) & (t < test_start - gap)] = "validation"
    split[t >= test_start] = "test"
    return split


def _metrics(y, p, threshold):
    """Metrics for one group of rows. Undefined values are None, never 0."""
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    warn = p >= threshold
    n, positives = len(y), int(y.sum())
    tp = int((warn & (y == 1)).sum())
    n_warn = int(warn.sum())
    precision = tp / n_warn if n_warn else None
    recall = tp / positives if positives else None
    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return {
        "n": n,
        "n_positive": positives,
        "base_rate": positives / n if n else None,
        "n_warned": n_warn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        # PR-AUC needs both classes present to mean anything.
        "pr_auc": float(average_precision_score(y, p)) if 0 < positives < n else None,
        "accuracy": float((warn == (y == 1)).mean()) if n else None,
    }


def score(y_true, y_score, transition, threshold=THRESHOLD):
    """Score predictions overall and on transition rows.

    y_true: 0/1 labels; y_score: a score in [0, 1] (a warning is raised when
    it is at least `threshold`); transition: 0/1 flags from labels.add_labels.
    Rows with a missing label are dropped first.

    Returns {"all": metrics, "transition": metrics}, where metrics is a dict
    of n, n_positive, base_rate, n_warned, precision, recall, f1, pr_auc,
    accuracy. A metric that cannot be computed (no warnings, no positives)
    is None rather than a misleading 0.
    """
    rows = pd.DataFrame({"y": y_true, "p": y_score, "tr": transition})
    rows = rows[rows["y"].notna()]
    if rows["p"].isna().any():
        raise ValueError("predictions are missing for some labeled rows")
    on_transition = rows[rows["tr"] == 1]
    return {
        "all": _metrics(rows["y"], rows["p"], threshold),
        "transition": _metrics(on_transition["y"], on_transition["p"], threshold),
    }


def format_metrics(m):
    """One line of text for a metrics dict, for printing tables."""
    def fmt(v):
        return "  n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:5.3f}"
    return (f"n={m['n']:>7} pos={m['n_positive']:>6} base={fmt(m['base_rate'])} "
            f"P={fmt(m['precision'])} R={fmt(m['recall'])} F1={fmt(m['f1'])} "
            f"PR-AUC={fmt(m['pr_auc'])} acc={fmt(m['accuracy'])}")
