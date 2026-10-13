"""Tests for evaluate.score. Each example is small enough to check by hand."""

import pandas as pd
import pytest

from pogoh.evaluate import format_metrics, score


def test_counts_precision_recall_f1_by_hand():
    # 10 rows, 4 positives. Warnings on rows 0, 1, 2, 4: 3 hits, 1 false alarm.
    y = [1, 1, 1, 1, 0, 0, 0, 0, 0, 0]
    p = [0.9, 0.8, 0.6, 0.1, 0.7, 0.2, 0.0, 0.0, 0.0, 0.0]
    m = score(y, p, transition=[0] * 10)["all"]
    assert m["n"] == 10 and m["n_positive"] == 4 and m["base_rate"] == 0.4
    assert m["n_warned"] == 4
    assert m["precision"] == pytest.approx(3 / 4)
    assert m["recall"] == pytest.approx(3 / 4)
    assert m["f1"] == pytest.approx(3 / 4)
    assert m["accuracy"] == pytest.approx(8 / 10)  # 1 miss + 1 false alarm


def test_threshold_moves_the_warning_line():
    y = [1, 0]
    p = [0.3, 0.1]
    assert score(y, p, [0, 0])["all"]["recall"] == 0.0  # 0.3 < 0.5
    assert score(y, p, [0, 0], threshold=0.2)["all"]["recall"] == 1.0


def test_pr_auc_is_one_for_a_perfect_ranking():
    y = [0, 0, 1, 1]
    p = [0.1, 0.2, 0.3, 0.4]  # all below the threshold, but ordered perfectly
    m = score(y, p, [0] * 4)["all"]
    assert m["pr_auc"] == pytest.approx(1.0)
    assert m["recall"] == 0.0  # the ranking is right; the threshold is the problem


def test_transition_rows_are_scored_separately():
    # Persistence-style scores: right on the steady rows, wrong on transitions.
    y = [1, 1, 0, 0]
    p = [1.0, 0.0, 0.0, 1.0]
    tr = [0, 1, 0, 1]
    result = score(y, p, tr)
    assert result["all"]["n"] == 4 and result["transition"]["n"] == 2
    assert result["transition"]["recall"] == 0.0
    assert result["transition"]["precision"] == 0.0
    assert result["transition"]["accuracy"] == 0.0


def test_undefined_metrics_are_none_not_zero():
    m = score([0, 0, 0], [0.1, 0.1, 0.1], [0, 0, 0])["all"]
    assert m["precision"] is None  # no warnings raised
    assert m["recall"] is None  # no positives exist
    assert m["f1"] is None and m["pr_auc"] is None
    assert m["accuracy"] == 1.0  # high accuracy, base rate 0: why both are shown


def test_rows_with_missing_labels_are_dropped():
    y = pd.array([1, pd.NA, 0], dtype="Int8")
    m = score(y, [0.9, 0.9, 0.1], pd.array([0, pd.NA, 0], dtype="Int8"))["all"]
    assert m["n"] == 2 and m["precision"] == 1.0


def test_missing_prediction_for_a_labeled_row_is_an_error():
    with pytest.raises(ValueError):
        score([1, 0], [0.5, float("nan")], [0, 0])


def test_no_transition_rows_gives_an_empty_group():
    m = score([1, 0], [0.9, 0.1], [0, 0])["transition"]
    assert m["n"] == 0 and m["base_rate"] is None and m["accuracy"] is None


def test_format_metrics_shows_na():
    line = format_metrics(score([0], [0.1], [0])["all"])
    assert "P=  n/a" in line and "acc=1.000" in line
