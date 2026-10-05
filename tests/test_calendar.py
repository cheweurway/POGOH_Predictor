"""Tests for the academic calendar file and flags."""

import datetime as dt

import pandas as pd

from pogoh.calendar import calendar_flags, load_calendar


def _flags(*dates):
    return calendar_flags(pd.Series([dt.date.fromisoformat(d) for d in dates]), load_calendar())


def test_real_calendar_loads():
    cal = load_calendar()
    assert set(cal["school"]) == {"CMU", "PITT"}


def test_cmu_fall_break():
    f = _flags("2026-10-09", "2026-10-12", "2026-10-16", "2026-10-19")
    assert f["cmu_in_term"].tolist() == [1, 1, 1, 1]
    assert f["cmu_no_classes"].tolist() == [0, 1, 1, 0]


def test_pitt_fall_break_differs_from_cmu():
    f = _flags("2026-10-09", "2026-10-12")
    assert f["pitt_no_classes"].tolist() == [1, 0]
    assert f["cmu_no_classes"].tolist() == [0, 1]


def test_outside_term_is_all_zero():
    f = _flags("2026-12-20")
    assert f.iloc[0].sum() == 0


def test_finals():
    f = _flags("2026-12-08", "2026-12-14")
    assert f["cmu_finals"].tolist() == [1, 1]
    assert f["pitt_finals"].tolist() == [1, 0]
