"""SCRUM-50 / SCRUM-52 / SCRUM-60 tests (pure rule functions, no database).

* SCRUM-50  build_activity_guidance   -- condition-aware activity guidance
* SCRUM-52  check_food_safety         -- Safe / Caution / Avoid with reasons
* SCRUM-60  build_grocery_list        -- grocery list, cost, budget status
            purchase_links            -- validated outbound links, data minimisation

All three are pure functions, so no Oracle connection or HTTP client is needed.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")

from backend.routers.health_profile_router import build_activity_guidance
from backend.services.food_checker import check_food_safety
from backend.services.grocery_service import (
    ALLOWED_HOSTS,
    build_grocery_list,
    is_safe_search_word,
    purchase_links,
)

CHICKEN_CURRY = {
    "name": "Chicken Curry", "ingredients": "Chicken, spices, yogurt", "allergens": "milk",
    "diet_tags": "non-veg", "sugar_g": 3, "glycemic_index": 50, "sodium_mg": 380, "saturated_fat_g": 8,
}
STEAMED_RICE = {
    "name": "Steamed Rice", "ingredients": "Rice, water", "allergens": None, "diet_tags": None,
    "sugar_g": 0.1, "glycemic_index": 73, "sodium_mg": 1, "saturated_fat_g": 0.1,
}
DAL = {
    "name": "Lentil Soup (Dal)", "ingredients": "Red lentils, turmeric, cumin", "allergens": None,
    "diet_tags": "vegetarian, vegan", "sugar_g": 2, "glycemic_index": 30, "sodium_mg": 300, "saturated_fat_g": 0.5,
}


# --------------------------------------------------------------------------- #
# SCRUM-50: activity guidance
# --------------------------------------------------------------------------- #
def test_guidance_knee_limitation_gives_low_impact_advice():
    guidance, warnings = build_activity_guidance("", "Light", "Bad knees")
    assert any("low-impact" in g for g in guidance)
    assert any("jumping" in w for w in warnings)


def test_guidance_covers_each_condition():
    conditions = "Hypertension, Diabetes, Asthma, Heart disease, Anemia, Pregnancy"
    guidance, warnings = build_activity_guidance(conditions, None, None)
    assert len(guidance) == 6
    assert "Talk to your doctor before starting a new exercise programme." in warnings


def test_guidance_without_conditions_uses_the_general_target():
    guidance, warnings = build_activity_guidance(None, None, None)
    assert guidance == ["Aim for at least 150 minutes of moderate physical activity per week."]
    assert warnings == []


def test_guidance_adds_an_activity_level_tip():
    guidance, _ = build_activity_guidance("", "Sedentary", "")
    assert any("10-minute walk" in g for g in guidance)


# --------------------------------------------------------------------------- #
# SCRUM-52: food safety checker
# --------------------------------------------------------------------------- #
def test_safety_requires_a_food_name():
    result = check_food_safety({"food": ""})
    assert result["status"] == "error"


def test_safety_allergy_is_avoid_with_reason():
    result = check_food_safety({"food": "Chicken Curry", "allergies": ["dairy"]}, dish=CHICKEN_CURRY)
    assert result["status"] == "Avoid"
    assert result["professional_review"] is True
    assert any("dairy" in r for r in result["reasons"])


def test_safety_uses_the_saved_profile():
    result = check_food_safety({"food": "Chicken Curry"}, profile={"food_preference": "Vegetarian"}, dish=CHICKEN_CURRY)
    assert result["status"] == "Avoid"
    assert any("vegetarian" in r for r in result["reasons"])


def test_safety_non_vegetarian_is_not_treated_as_vegetarian():
    result = check_food_safety({"food": "Chicken Curry"}, profile={"food_preference": "Non-Vegetarian"}, dish=CHICKEN_CURRY)
    assert result["status"] == "Safe"


def test_safety_high_gi_is_caution_for_diabetes():
    result = check_food_safety({"food": "rice", "conditions": ["Type 2 diabetes"]}, dish=STEAMED_RICE)
    assert result["status"] == "Caution"
    assert any("glycemic" in r for r in result["reasons"])


def test_safety_unknown_food_is_never_called_safe():
    result = check_food_safety({"food": "pizza"})
    assert result["status"] == "Caution"
    assert result["professional_review"] is True


def test_safety_egg_allergy_does_not_flag_a_dish_without_egg():
    result = check_food_safety({"food": "dal", "allergies": ["egg"]}, dish=DAL)
    assert result["status"] == "Safe"


def test_safety_every_non_safe_result_has_a_reason():
    for args in (({"food": "pizza"},), ({"food": "rice", "conditions": "diabetes"}, None, STEAMED_RICE)):
        result = check_food_safety(*args)
        assert result["status"] != "Safe" and result["reasons"]


# --------------------------------------------------------------------------- #
# SCRUM-60: grocery list + purchase links
# --------------------------------------------------------------------------- #
def _days(n=7):
    return [{"day": d + 1, "meals": [{"meal_type": "Lunch", "items": ["Rice", "Dal", "Salad"]},
                                     {"meal_type": "Dinner", "items": ["Khichuri", "Curd"]}]} for d in range(n)]


def _item(result, key):
    return next(i for c in result["categories"] for i in c["items"] if i["key"] == key)


def test_grocery_aggregates_quantities_across_dishes():
    result = build_grocery_list(_days())
    rice = _item(result, "rice")
    assert rice["servings"] == 14  # Rice x7 + Khichuri x7
    assert set(rice["used_in"]) == {"Rice", "Khichuri"}


def test_grocery_without_prices_never_guesses_a_cost():
    result = build_grocery_list(_days())
    assert result["summary"]["total_bdt"] is None
    assert all(i["estimated_cost_bdt"] is None for c in result["categories"] for i in c["items"])


def test_grocery_totals_and_budget_status():
    prices = [{"item_name": n, "unit": "1 serving", "price_bdt": p, "price_date": "2026-10-20"}
              for n, p in (("Rice", 15), ("Dal", 12), ("Salad", 10), ("Khichuri", 30), ("Curd", 20))]
    result = build_grocery_list(_days(), prices, monthly_budget=2000)
    assert result["summary"]["total_bdt"] == 7 * (15 + 12 + 10 + 30 + 20)
    assert result["summary"]["budget"]["status"] == "above"  # 87/day x 30 = 2610 > 2000
    within = build_grocery_list(_days(), prices, monthly_budget=5000)
    assert within["summary"]["budget"]["status"] == "within"


def test_grocery_links_only_go_to_allowed_https_shops():
    links = purchase_links("rice")["links"]
    assert links
    for link in links:
        assert link["url"].startswith("https://")
        assert any(host in link["url"] for host in ALLOWED_HOSTS)


def test_grocery_link_never_carries_health_or_free_text():
    assert is_safe_search_word("rice")
    assert not is_safe_search_word("diabetes")
    assert not is_safe_search_word("rice; drop table")
    assert purchase_links("diabetes")["available"] is False


def test_grocery_fresh_items_have_a_graceful_no_link_message():
    result = build_grocery_list(_days())
    salad = _item(result, "salad")
    assert salad["purchase"]["available"] is False
    assert salad["purchase"]["reason"]
