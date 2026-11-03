"""SCRUM-58 tests: meal-plan cost estimation (SRS 3.3.3.1).

Runs WITHOUT a live Oracle connection: the estimator is called directly and
the route is driven with its loaders replaced by small fakes.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")

import pytest
from fastapi import HTTPException

from backend.core.security import Principal, require_patient
from backend.routers import cost_router as router
from backend.services.cost_estimator import estimate_plan_cost
from backend.services.grocery_service import build_grocery_list, price_plan_item
from backend.services.optimizer_service import build_context, grocery_swaps
from backend.services.price_service import SEED_PRICES

PATIENT = Principal(subject="5", role="PATIENT")
PRICES = [dict(p) for p in SEED_PRICES]


def plan(*meals, days=1):
    """meals: lists of item names, one list per meal."""
    return [{"day": d + 1, "meals": [{"meal_type": f"Meal {i + 1}", "items": list(m)}
                                     for i, m in enumerate(meals)]} for d in range(days)]


def weekly_budget(per_week):
    return {"found": True, "mode": "WEEKLY", "amount": per_week, "per_day": per_week / 7,
            "per_week": per_week, "per_month": per_week / 7 * 30}


# --------------------------------------------------------------------------- #
# Prices: exact names only (same rules as the grocery list)
# --------------------------------------------------------------------------- #
def test_oats_porridge_is_never_priced_as_milk_alone():
    price = price_plan_item("Oats porridge with milk", PRICES)
    assert price["cost"] == 22.0 and price["complete"] is False   # milk priced, oats not
    assert price["parts"] == 2 and price["priced_parts"] == 1


def test_mixed_fruit_salad_is_never_priced_as_cucumber_salad():
    assert price_plan_item("Mixed fruit salad", PRICES)["cost"] is None


def test_roti_is_priced_from_atta_not_from_bread():
    assert price_plan_item("Roti", PRICES)["cost"] == 4.2
    assert price_plan_item("Whole wheat bread", PRICES)["cost"] is None


def test_khichuri_is_priced_from_rice_and_dal():
    price = price_plan_item("Khichuri", PRICES)
    assert price["cost"] == round(3.94 + 2.70, 2) and price["complete"] is True


# --------------------------------------------------------------------------- #
# Missing prices are never shown as 0
# --------------------------------------------------------------------------- #
def test_meal_with_no_recorded_price_is_none_not_zero():
    result = estimate_plan_cost(plan(["Banana", "Whole wheat bread"], ["Rice", "Dal"]), PRICES)
    breakfast, lunch = result["days"][0]["meals"]
    assert breakfast["cost_bdt"] is None and breakfast["complete"] is False
    assert lunch["cost_bdt"] == round(3.94 + 2.70) and lunch["complete"] is True


def test_partly_priced_meal_and_day_are_marked_partial():
    result = estimate_plan_cost(plan(["Rice", "Banana"]), PRICES)
    assert result["days"][0]["meals"][0]["complete"] is False
    assert result["days"][0]["complete"] is False and result["complete"] is False
    assert {"name": "Banana", "occurrences": 1} in result["missing_prices"]


def test_nothing_priced_gives_no_totals():
    result = estimate_plan_cost(plan(["Banana"]), PRICES)
    assert result["per_day_bdt"] is None and result["per_month_bdt"] is None
    assert result["days"][0]["cost_bdt"] is None


def test_partly_priced_dishes_are_listed():
    result = estimate_plan_cost(plan(["Oats porridge with milk"]), PRICES)
    assert result["partly_priced"] == [{"name": "Oats porridge with milk", "occurrences": 1}]


# --------------------------------------------------------------------------- #
# Totals and budget
# --------------------------------------------------------------------------- #
def test_totals_scale_from_the_plan_days():
    result = estimate_plan_cost(plan(["Rice", "Dal"], days=7), PRICES)
    assert result["per_day_bdt"] == round(3.94 + 2.70)
    assert result["per_week_bdt"] == round((3.94 + 2.70) * 7)
    assert result["per_month_bdt"] == round((3.94 + 2.70) * 30)
    assert result["complete"] is True and result["coverage_percent"] == 100


def test_estimate_matches_the_grocery_list_total():
    days = plan(["Rice", "Fish curry", "Khichuri"], ["Oats porridge with milk", "Roti"], days=7)
    estimate = estimate_plan_cost(days, PRICES)
    grocery = build_grocery_list(days, PRICES, None)
    assert estimate["per_week_bdt"] == grocery["summary"]["total_bdt"]


def test_budget_status_is_within_so_far_when_prices_are_missing():
    result = estimate_plan_cost(plan(["Rice", "Banana"], days=7), PRICES, budget=weekly_budget(1400))
    assert result["budget"]["status"] == "within_so_far"


def test_budget_status_within_only_when_everything_is_priced():
    result = estimate_plan_cost(plan(["Rice", "Dal"], days=7), PRICES, budget=weekly_budget(1400))
    assert result["budget"]["status"] == "within" and result["budget"]["mode"] == "WEEKLY"


def test_budget_status_above_even_with_missing_prices():
    result = estimate_plan_cost(plan(["Fish curry", "Banana"], days=7), PRICES, budget=weekly_budget(100))
    assert result["budget"]["status"] == "above" and result["budget"]["difference_bdt"] < 0


def test_every_budget_mode_is_compared_per_month():
    daily = {"found": True, "mode": "DAILY", "amount": 200, "per_day": 200, "per_week": 1400, "per_month": 6000}
    result = estimate_plan_cost(plan(["Rice"]), PRICES, budget=daily)
    assert result["budget"]["monthly_budget_bdt"] == 6000 and result["budget"]["budget_per_day_bdt"] == 200


def test_no_budget_is_reported_not_guessed():
    assert estimate_plan_cost(plan(["Rice"]), PRICES)["budget"]["status"] == "not_set"


def test_saved_swaps_are_included():
    days = plan(["Fish curry"], days=7)
    swap_map, _a, _p = grocery_swaps([{"swap_id": 1, "from_item": "fish", "to_item": "dal"}],
                                     build_context({}, []), PRICES)
    before = estimate_plan_cost(days, PRICES)["per_week_bdt"]
    after = estimate_plan_cost(days, PRICES, swaps=swap_map)["per_week_bdt"]
    assert after == round(7 * 2.5 * 2.70) and after < before


# --------------------------------------------------------------------------- #
# Route
# --------------------------------------------------------------------------- #
class FakeConnection:
    def cursor(self):
        return self

    def close(self):
        pass


@pytest.fixture
def route(monkeypatch):
    monkeypatch.setattr(router, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(router, "_load_prices", lambda cursor: PRICES)
    monkeypatch.setattr(router, "_load_budget", lambda cursor, pid: weekly_budget(1400))
    monkeypatch.setattr(router, "_load_swaps", lambda cursor, pid, prices: ({}, [], []))
    monkeypatch.setattr(router, "_load_plan",
                        lambda cursor, pid: ({"plan_id": 3, "days": plan(["Rice", "Dal"], days=7)}, "accepted"))
    return router


def test_estimate_route_is_patient_only():
    import inspect
    assert inspect.signature(router.read_cost_estimate).parameters["principal"].default.dependency is require_patient


def test_estimate_route_returns_the_estimate(route):
    result = route.read_cost_estimate(principal=PATIENT)
    assert result["found"] and result["plan_id"] == 3 and result["budget"]["status"] == "within"


def test_no_plan_gives_a_clear_message(route, monkeypatch):
    monkeypatch.setattr(router, "_load_plan", lambda cursor, pid: (None, None))
    result = route.read_cost_estimate(principal=PATIENT)
    assert result["found"] is False and result["message"]


def test_invalid_token_subject_is_401():
    with pytest.raises(HTTPException) as error:
        router.read_cost_estimate(principal=Principal(subject="abc", role="PATIENT"))
    assert error.value.status_code == 401
