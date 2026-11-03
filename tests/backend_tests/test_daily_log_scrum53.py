"""SCRUM-53 tests: daily logging and dashboard data (SRS 3.2.3.2, 3.2.3.3).

Covers the input rules of a log entry, the day summary and the trend used by
the dashboard chart. These are pure functions, so no database is needed.
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")

import pytest
from pydantic import ValidationError

from backend.schemas import daily_log as schema
from backend.schemas.daily_log import DailyLogEntryIn
from backend.services import daily_log_service as svc

TODAY = date.today()


def entry(**data):
    return DailyLogEntryIn(**data)


# --------------------------------------------------------------------------- #
# Entry rules
# --------------------------------------------------------------------------- #
def test_date_defaults_to_today():
    assert entry(log_type="water", quantity=250).log_date == TODAY


def test_future_date_is_rejected():
    with pytest.raises(ValidationError):
        entry(log_type="water", quantity=250, log_date=TODAY + timedelta(days=1))


def test_entries_older_than_the_back_fill_window_are_rejected():
    with pytest.raises(ValidationError):
        entry(log_type="water", quantity=250,
              log_date=TODAY - timedelta(days=schema.MAX_BACKFILL_DAYS + 1))


def test_unknown_log_type_is_rejected():
    with pytest.raises(ValidationError):
        entry(log_type="sleep", quantity=8)


def test_meal_needs_a_food_and_a_slot():
    with pytest.raises(ValidationError):
        entry(log_type="meal", slot="lunch")          # no food
    with pytest.raises(ValidationError):
        entry(log_type="meal", title="Rice")          # no slot
    assert entry(log_type="meal", title="Rice", slot="lunch").slot == "LUNCH"


def test_water_must_be_between_1_and_3000_ml_per_entry():
    with pytest.raises(ValidationError):
        entry(log_type="water", quantity=0)
    with pytest.raises(ValidationError):
        entry(log_type="water", quantity=schema.MAX_WATER_ML_PER_ENTRY + 1)
    assert entry(log_type="water", quantity=500).title == "Water"


def test_completed_activity_needs_minutes_but_skipped_does_not():
    with pytest.raises(ValidationError):
        entry(log_type="activity", title="Brisk walking")
    skipped = entry(log_type="activity", title="Brisk walking", status="skipped")
    assert skipped.status == "SKIPPED"


def test_medication_needs_taken_missed_or_skipped():
    with pytest.raises(ValidationError):
        entry(log_type="medication", title="Iron tablet")
    assert entry(log_type="medication", title="Iron tablet", status="taken").status == "TAKEN"


def test_goal_ignores_a_quantity():
    goal = entry(log_type="goal", title="No sugary drinks", status="achieved", quantity=5)
    assert goal.quantity is None


def test_time_must_be_hh_mm():
    with pytest.raises(ValidationError):
        entry(log_type="water", quantity=250, log_time="25:00")


def test_units_follow_the_log_type():
    assert entry(log_type="water", quantity=250).unit == "ml"
    assert entry(log_type="activity", title="Walk", quantity=20).unit == "min"


# --------------------------------------------------------------------------- #
# Day summary and trend
# --------------------------------------------------------------------------- #
def test_summary_adds_up_a_day():
    entries = [
        {"log_type": "WATER", "quantity": 500}, {"log_type": "WATER", "quantity": 250},
        {"log_type": "MEAL", "quantity": 1},
        {"log_type": "ACTIVITY", "status": "COMPLETED", "quantity": 30},
        {"log_type": "ACTIVITY", "status": "SKIPPED", "quantity": 0},
        {"log_type": "MEDICATION", "status": "TAKEN"}, {"log_type": "MEDICATION", "status": "MISSED"},
        {"log_type": "GOAL", "status": "ACHIEVED"},
    ]
    s = svc.summarize(entries)
    assert s["water_ml"] == 750 and s["meals"] == 1
    assert s["activity_min"] == 30 and s["activity_sessions"] == 1   # skipped not counted
    assert (s["meds_taken"], s["meds_total"]) == (1, 2)
    assert (s["goals_achieved"], s["goals_total"]) == (1, 1)


def test_trend_fills_days_without_entries_with_zero():
    start = date(2026, 10, 19)
    end = date(2026, 10, 25)
    trend = svc.build_trend([(date(2026, 10, 20), "WATER", None, 1000)], start, end)
    assert len(trend["points"]) == 7
    assert trend["days_logged"] == 1
    assert trend["totals"]["water_ml"] == 1000
    assert trend["points"][0]["water_ml"] == 0


def test_trend_ignores_rows_outside_the_range():
    start = date(2026, 10, 19)
    trend = svc.build_trend([(date(2026, 9, 1), "WATER", None, 999)], start, start + timedelta(days=6))
    assert trend["totals"]["water_ml"] == 0
