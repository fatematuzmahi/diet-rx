"""SCRUM-51 tests: smart notifications and reminders (SRS 3.1.3.4).

Covers the reminder input rules, which reminders are due, the rule-based
safety alerts and the adherence statistics. These are pure functions, so no
database is needed.
"""

import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")

import pytest
from pydantic import ValidationError

from backend.schemas.notification import DAYS, AckIn, ReminderIn
from backend.services import notification_service as svc

MONDAY = date(2026, 10, 26)


def reminder(**overrides):
    data = dict(reminder_type="MEDICATION", title="Iron tablet", remind_time="08:30")
    data.update(overrides)
    return ReminderIn(**data)


def stored(nid=1, rtype="WATER", time="08:00", days=None, enabled=True, title="Drink water"):
    return {"notification_id": nid, "reminder_type": rtype, "title": title, "message": None,
            "remind_time": time, "repeat_days": days or list(DAYS), "enabled": enabled}


# --------------------------------------------------------------------------- #
# Reminder input rules
# --------------------------------------------------------------------------- #
def test_reminder_defaults_to_every_day():
    assert reminder().repeat_days == list(DAYS)


def test_reminder_activity_is_stored_as_exercise():
    assert reminder(reminder_type="activity").reminder_type == "EXERCISE"


def test_reminder_rejects_an_unknown_type():
    with pytest.raises(ValidationError):
        reminder(reminder_type="party")


def test_reminder_time_must_be_hh_mm():
    with pytest.raises(ValidationError):
        reminder(remind_time="8:30pm")


def test_reminder_accepts_browser_time_with_seconds():
    assert reminder(remind_time="08:30:00").remind_time == "08:30"


def test_reminder_daily_means_all_seven_days():
    assert reminder(repeat_days=["daily"]).repeat_days == list(DAYS)


def test_reminder_rejects_an_unknown_day():
    with pytest.raises(ValidationError):
        reminder(repeat_days=["Funday"])


def test_reminder_needs_a_title():
    with pytest.raises(ValidationError):
        reminder(title="   ")


def test_ack_accepts_only_done_or_dismissed():
    assert AckIn(action="done").action == "DONE"
    with pytest.raises(ValidationError):
        AckIn(action="snooze")


# --------------------------------------------------------------------------- #
# Which reminders are due
# --------------------------------------------------------------------------- #
def test_reminder_is_due_once_its_time_has_arrived():
    due = svc.due_reminders([stored(time="08:00")], MONDAY, "08:15")
    assert len(due) == 1 and due[0]["category"] == "REMINDER"


def test_reminder_is_not_due_before_its_time():
    assert svc.due_reminders([stored(time="09:00")], MONDAY, "08:15") == []


def test_disabled_reminder_is_never_due():
    assert svc.due_reminders([stored(enabled=False)], MONDAY, "23:59") == []


def test_reminder_is_only_due_on_its_days():
    assert svc.due_reminders([stored(days=["SAT", "SUN"])], MONDAY, "23:59") == []


def test_due_event_key_is_unique_per_reminder_and_day():
    due = svc.due_reminders([stored(nid=7)], MONDAY, "23:59")
    assert due[0]["event_key"] == "R7:2026-10-26"


# --------------------------------------------------------------------------- #
# Safety alerts
# --------------------------------------------------------------------------- #
def test_allergy_matches_plural_and_dish_allergens():
    assert svc.allergy_hits("Peanuts", "Peanut chikki", None) == ["peanut"]
    assert svc.allergy_hits("Milk", "Chicken Curry", "milk") == ["milk"]


def test_no_allergy_recorded_means_no_alert():
    assert svc.allergy_hits("None", "Peanut chikki", "peanut") == []


def test_logged_allergen_meal_raises_a_high_alert():
    alerts = svc.safety_alerts(MONDAY, [], [{"log_id": 3, "title": "Peanut chikki", "allergens": None}],
                               "Peanuts", False)
    assert alerts[0]["severity"] == "HIGH" and alerts[0]["category"] == "SAFETY"
    assert "not a diagnosis" in alerts[0]["message"]


def test_missed_medicine_raises_a_caution_alert():
    alerts = svc.safety_alerts(MONDAY, [{"log_id": 4, "title": "Metformin"}], [], None, False)
    assert alerts[0]["severity"] == "CAUTION"
    assert "Don't change your dose" in alerts[0]["message"]


def test_plan_needing_review_raises_an_alert():
    alerts = svc.safety_alerts(MONDAY, [], [], None, True)
    assert alerts and alerts[0]["title"] == "Your plan needs professional review"


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #
def test_stats_count_done_dismissed_pending_and_rate():
    rows = [("REMINDER", "WATER", "DONE"), ("REMINDER", "WATER", None),
            ("REMINDER", "MEAL", "DISMISSED"), ("SAFETY", "MEAL", "DONE")]
    stats = svc.build_stats(rows, 7)
    assert (stats["delivered"], stats["done"], stats["dismissed"], stats["pending"]) == (4, 2, 1, 1)
    assert stats["done_rate"] == 50
    assert stats["safety_alerts"] == 1


def test_stats_with_no_events_have_zero_rate():
    assert svc.build_stats([], 7)["done_rate"] == 0
