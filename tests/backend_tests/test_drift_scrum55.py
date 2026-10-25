"""SCRUM-55 tests: dietary drift detection + adaptive compensator (SRS 3.2.3.4).

Pure rule engine, so no database is needed. Covers the Jira test list:
skip / overeat / substitute, plus the safety limits, restriction guard,
explanations and the suggestion-only / professional-review thresholds.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")

from backend.services.drift_service import detect_and_compensate

MEALS = [
    {"meal_type": "Breakfast", "time": "08:00", "items": ["Oats porridge with milk"], "calories": 450},
    {"meal_type": "Mid-morning snack", "time": "10:30", "items": ["Seasonal fruit"], "calories": 180},
    {"meal_type": "Lunch", "time": "13:00", "items": ["Rice", "Dal"], "calories": 540},
    {"meal_type": "Afternoon snack", "time": "16:00", "items": ["Sprouts"], "calories": 180},
    {"meal_type": "Dinner", "time": "19:30", "items": ["Roti", "Dal"], "calories": 450},
]
TARGET = 1800


def ate(meal_type, quantity=1):
    """The entry the Meal Plan page writes when the patient taps 'I ate this'."""
    return {"log_type": "MEAL", "slot": "LUNCH", "title": meal_type, "quantity": quantity,
            "notes": f"From today's plan: {meal_type}"}


def meal(result, meal_type):
    return next(m for m in result["meals"] if m["meal_type"] == meal_type)


def run(entries, now, **kw):
    return detect_and_compensate(MEALS, entries, now, TARGET, **kw)


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #
def test_nothing_logged_before_the_first_meal_is_on_track():
    assert run([], "07:00")["status"] == "on_track"


def test_following_the_plan_is_on_track():
    result = run([ate("Breakfast"), ate("Mid-morning snack"), ate("Lunch")], "14:00")
    assert result["status"] == "on_track"
    assert result["events"] == []


def test_skip_is_detected_after_the_grace_period():
    early = run([ate("Breakfast")], "11:30")          # snack at 10:30, only 60 min ago
    late = run([ate("Breakfast")], "12:01")           # 91 min later
    assert meal(early, "Mid-morning snack")["state"] == "pending"
    assert meal(late, "Mid-morning snack")["state"] == "skipped"
    assert any(e["type"] == "skipped" for e in late["events"])


def test_overeat_is_detected_as_extra_intake():
    result = run([ate("Breakfast"), ate("Mid-morning snack"), ate("Lunch", 2)], "14:00")
    assert any(e["type"] == "extra" for e in result["events"])
    assert result["consumed_kcal"] == 450 + 180 + 1080


def test_substitute_uses_dish_dna_calories():
    entry = {"log_type": "MEAL", "slot": "LUNCH", "title": "Khichuri", "dish_id": 9, "quantity": 1,
             "log_time": "13:05"}
    result = run([ate("Breakfast"), ate("Mid-morning snack"), entry], "14:00", dishes={9: {"calories": 400}})
    lunch = meal(result, "Lunch")
    assert lunch["state"] == "substituted" and lunch["actual_kcal"] == 400
    assert any(e["type"] == "substituted" and e["delta_kcal"] == -140 for e in result["events"])


def test_extra_food_after_a_meal_was_eaten():
    extra = {"log_type": "MEAL", "slot": "BREAKFAST", "title": "Paratha", "dish_id": 5, "quantity": 1}
    result = run([ate("Breakfast"), extra], "09:00", dishes={5: {"calories": 300}})
    assert any(e["type"] == "extra" and e["title"] == "Paratha" for e in result["events"])


# --------------------------------------------------------------------------- #
# Recalculation within safety limits
# --------------------------------------------------------------------------- #
def test_small_extra_is_rebalanced_automatically_within_the_limit():
    extra = {"log_type": "MEAL", "slot": "BREAKFAST", "title": "Paratha", "dish_id": 5, "quantity": 1}
    result = run([ate("Breakfast"), extra], "09:00", dishes={5: {"calories": 300}})
    assert result["status"] == "adjusted"
    for m in result["meals"]:
        if m["adjusted_kcal"] is not None:
            assert abs(m["change_percent"]) <= 25


def test_change_never_exceeds_25_percent():
    result = run([ate("Breakfast"), ate("Mid-morning snack")], "15:00")   # lunch skipped
    assert result["needed_change_percent"] > 25
    assert result["applied_change_percent"] == 25
    assert meal(result, "Dinner")["adjusted_kcal"] == round(450 * 1.25)


def test_diabetes_uses_a_tighter_15_percent_limit():
    result = run([ate("Breakfast"), ate("Lunch")], "14:00", conditions="Type 2 diabetes")
    assert result["limit_percent"] == 15
    assert result["applied_change_percent"] <= 15


def test_pregnancy_never_makes_a_meal_smaller():
    result = run([ate("Breakfast"), ate("Mid-morning snack"), ate("Lunch", 1.5)], "14:00", conditions="Pregnancy")
    assert all((m["adjusted_kcal"] or m["planned_kcal"]) >= m["planned_kcal"] for m in result["meals"])
    assert any("pregnancy" in n.lower() for n in result["notes"])


def test_no_new_food_is_added_only_portions_change():
    result = run([ate("Breakfast"), ate("Lunch")], "14:00")
    for before, after in zip(MEALS, result["meals"]):
        assert after["items"] == before["items"]


# --------------------------------------------------------------------------- #
# Explanation, thresholds and restriction guard
# --------------------------------------------------------------------------- #
def test_every_adjusted_meal_explains_why():
    result = run([ate("Breakfast"), ate("Lunch")], "14:00")
    adjusted = [m for m in result["meals"] if m["adjusted_kcal"] is not None]
    assert adjusted
    assert all(m["explanation"] and "skipped" in m["explanation"] for m in adjusted)


def test_beyond_the_limit_is_suggestion_only():
    result = run([ate("Breakfast"), ate("Mid-morning snack")], "15:00")
    assert result["status"] == "suggestion_only"


def test_unknown_calories_are_never_guessed():
    pizza = {"log_type": "MEAL", "slot": "LUNCH", "title": "Pizza", "quantity": 1}
    result = run([ate("Breakfast"), ate("Mid-morning snack"), pizza], "14:00")
    assert result["status"] == "suggestion_only"
    assert result["unknown_calories"] == ["Pizza"]
    assert all(m["adjusted_kcal"] is None for m in result["meals"])


def test_logged_allergen_needs_professional_review():
    chikki = {"log_type": "MEAL", "slot": "SNACK", "title": "Peanut chikki", "quantity": 1, "log_time": "10:30"}
    result = run([ate("Breakfast"), chikki], "11:00", allergies="Peanuts")
    assert result["status"] == "professional_review"
    assert any(e["type"] == "allergy" and e["severity"] == "HIGH" for e in result["events"])
    assert all(m["adjusted_kcal"] is None for m in result["meals"])


def test_eating_far_more_than_the_target_needs_review():
    result = run([ate("Breakfast"), ate("Mid-morning snack"), ate("Lunch", 4)], "14:00")
    assert result["consumed_kcal"] > TARGET * 1.25
    assert result["status"] == "professional_review"
    assert result["professional_review"] is True


def test_finished_day_is_complete():
    result = run([ate(m["meal_type"]) for m in MEALS], "21:00")
    assert result["status"] == "day_complete"
    assert result["disclaimer"]
