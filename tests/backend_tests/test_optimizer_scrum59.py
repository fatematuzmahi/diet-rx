"""SCRUM-59 tests: budget-aware Bengali food optimizer + local alternatives
(SRS 3.3.3.2, 3.3.3.5).

Runs WITHOUT a live Oracle connection: the rules are called directly and the
routes use a small in-memory fake database.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")

import pytest
from fastapi import HTTPException

from backend.core.security import Principal, require_patient
from backend.routers import optimizer_router as router
from backend.schemas.optimizer import SwapIn
from backend.services import optimizer_service as opt
from backend.services.grocery_service import build_grocery_list
from backend.services.price_service import SEED_PRICES

PATIENT = Principal(subject="5", role="PATIENT")
OTHER = Principal(subject="9", role="PATIENT")
PRICES = [dict(p, stale=False) for p in SEED_PRICES]


def ctx(allergies=None, conditions=None, preference=None, rx=None):
    return opt.build_context(
        {"allergies": allergies, "conditions": conditions, "food_preference": preference},
        [{"restrictions": rx, "source": "Dr. Test"}] if rx else [],
    )


def alt_keys(result):
    return [a["key"] for a in result["alternatives"]]


def week_plan(*items):
    return [{"meals": [{"items": list(items)}]} for _ in range(7)]


# --------------------------------------------------------------------------- #
# Safety first (SRS 3.3.3.2.3, 3.3.3.5.4)
# --------------------------------------------------------------------------- #
def test_allergy_food_is_never_suggested():
    result = opt.alternatives_for("chicken", ctx(allergies="Lentils"), PRICES)
    assert "dal" not in alt_keys(result)
    assert any(n["key"] == "dal" and "allergy" in n["reason"] for n in result["not_suggested"])


def test_vegetarian_never_gets_fish_chicken_or_egg():
    c = ctx(preference="Vegetarian")
    for key in ("dal", "chana", "sprouts", "paneer"):
        assert not {"fish", "chicken", "eggs"} & set(alt_keys(opt.alternatives_for(key, c, PRICES)))


def test_non_vegetarian_preference_is_not_read_as_vegetarian():
    assert "vegetarian" not in ctx(preference="Non-vegetarian")["tokens"]
    assert "chicken" in alt_keys(opt.alternatives_for("fish", ctx(preference="Non-vegetarian"), PRICES, limit=None))


def test_the_page_shows_at_most_three_best_alternatives():
    result = opt.alternatives_for("fish", ctx(), PRICES)
    savings = [a["saving_per_serving"] for a in result["alternatives"]]
    assert len(savings) <= 3 and savings == sorted(savings, reverse=True)


def test_vegan_also_excludes_dairy():
    assert opt.screen_option("milk", ctx(preference="Vegan"))[0] is False
    assert opt.screen_option("eggs", ctx(preference="Vegan"))[0] is False


def test_gluten_free_never_gets_wheat():
    assert opt.screen_option("atta", ctx(preference="Gluten-free"))[0] is False


def test_prescription_that_limits_a_food_blocks_it():
    c = ctx(rx="Avoid chana. Eat more vegetables.")
    assert opt.screen_option("chana", c)[0] is False
    assert opt.screen_option("vegetables", c)[0] is True   # "eat more" is not a limit


def test_kidney_condition_keeps_pulses_for_the_doctor():
    ok, why = opt.screen_option("dal", ctx(conditions="Chronic kidney disease"))
    assert ok is False and "kidney" in why


def test_diabetes_is_not_offered_sugary_fruit():
    assert opt.screen_option("banana", ctx(conditions="Type 2 diabetes"))[0] is False


def test_whole_grain_is_never_swapped_for_white_rice():
    result = opt.alternatives_for("atta", ctx(), PRICES)
    assert "rice" not in alt_keys(result)
    assert any(n["key"] == "rice" and "fibre" in n["reason"] for n in result["not_suggested"])


def test_food_without_a_recorded_price_gets_no_cheaper_claim():
    for key in ("oats", "paneer", "curd", "apple"):
        result = opt.alternatives_for(key, ctx(), PRICES)
        assert result["alternatives"] == [] and "No local price" in result["message"]


def test_unsuitable_food_in_the_plan_gets_a_safe_swap_even_if_not_cheaper():
    result = opt.alternatives_for("fish", ctx(preference="Vegetarian"), PRICES)
    assert result["suitable"] is False and result["unsuitable_reason"]
    assert result["alternatives"] and all(a["key"] in ("dal", "chana", "sprouts") for a in result["alternatives"])


# --------------------------------------------------------------------------- #
# Same food group, priced, cheaper (SRS 3.3.3.2.1-2)
# --------------------------------------------------------------------------- #
def test_alternatives_stay_in_the_same_food_group():
    for key in ("fish", "rice", "salad"):
        result = opt.alternatives_for(key, ctx(), PRICES)
        assert all(opt.OPTIONS[a["key"]]["role"] == opt.OPTIONS[key]["role"] for a in result["alternatives"])


def test_only_cheaper_and_priced_foods_are_suggested():
    result = opt.alternatives_for("fish", ctx(), PRICES)
    assert all(a["saving_per_serving"] > 0 for a in result["alternatives"])
    assert "eggs" not in alt_keys(result)          # 3 eggs cost more than 1 piece of fish
    assert any(n["key"] == "paneer" and "price" in n["reason"] for n in result["not_suggested"])


def test_protein_swap_keeps_about_the_same_protein_and_the_maths_is_right():
    dal = next(a for a in opt.alternatives_for("fish", ctx(), PRICES, servings_per_week=7)["alternatives"]
               if a["key"] == "dal")
    assert dal["servings_ratio"] == 2.5
    assert dal["cost_per_original_serving"] == 6.75            # 2.5 x 2.70
    assert dal["saving_per_serving"] == 28.75                  # 35.50 - 6.75
    assert dal["saving_per_week"] == 201.25


def test_every_suggestion_explains_why_and_shows_its_source():
    for a in opt.alternatives_for("fish", ctx(), PRICES)["alternatives"]:
        assert len(a["why"]) >= 3 and a["source"] and a["source_url"].startswith("https://") and a["price_date"]


def test_no_safe_cheaper_food_gives_a_clear_message():
    result = opt.alternatives_for("dal", ctx(), PRICES)
    assert result["alternatives"] == [] and result["message"]


def test_no_prices_means_no_guessed_suggestions():
    result = opt.alternatives_for("fish", ctx(), [])
    assert result["alternatives"] == []


# --------------------------------------------------------------------------- #
# Whole plan + grocery list
# --------------------------------------------------------------------------- #
def test_summary_compares_the_plan_with_the_weekly_budget():
    days = week_plan("Rice", "Fish curry", "Dal")
    s = opt.build_summary(days, PRICES, ctx(), {"found": True, "per_week": 200, "mode": "WEEKLY"})
    assert s["cost_per_week"] == round(7 * (3.94 + 35.50 + 2.70))
    assert s["status"] == "above" and s["over_budget_by"] == s["cost_per_week"] - 200
    assert s["ideas"][0]["key"] == "fish" and s["possible_saving_per_week"] > 0


def test_summary_without_a_budget_still_shows_savings():
    s = opt.build_summary(week_plan("Fish curry"), PRICES, ctx(), None)
    assert s["status"] == "not_set" and s["ideas"]


def test_saved_swap_is_applied_to_the_grocery_list():
    days = week_plan("Rice", "Fish curry")
    swap_map, active, paused = opt.grocery_swaps([{"swap_id": 1, "from_item": "fish", "to_item": "dal"}], ctx(), PRICES)
    result = build_grocery_list(days, PRICES, None, swaps=swap_map)
    keys = [i["key"] for c in result["categories"] for i in c["items"]]
    dal = next(i for c in result["categories"] for i in c["items"] if i["key"] == "dal")
    assert "fish" not in keys and dal["servings"] == 17.5 and dal["estimated_cost_bdt"] == round(17.5 * 2.70)
    assert any("swap for" in u for u in dal["used_in"]) and active and not paused


def test_chained_swaps_move_every_serving_to_the_last_food():
    rows = [{"swap_id": 1, "from_item": "sprouts", "to_item": "chana"},
            {"swap_id": 2, "from_item": "chana", "to_item": "dal"}]
    swap_map, active, _ = opt.grocery_swaps(rows, ctx(), PRICES)
    result = build_grocery_list(week_plan("Sprouts", "Roasted chana"), PRICES, None, swaps=swap_map)
    keys = {i["key"]: i for c in result["categories"] for i in c["items"]}
    assert "sprouts" not in keys and "chana" not in keys and keys["dal"]["servings"] == 14
    assert len(active) == 2


def test_grocery_list_without_swaps_is_unchanged():
    days = week_plan("Rice", "Fish curry")
    assert build_grocery_list(days, PRICES, None) == build_grocery_list(days, PRICES, None, swaps={})


def test_a_saved_swap_that_became_unsafe_is_paused_not_applied():
    swap_map, active, paused = opt.grocery_swaps(
        [{"swap_id": 1, "from_item": "fish", "to_item": "dal"}], ctx(allergies="lentil"), PRICES)
    assert swap_map == {} and active == [] and paused and paused[0]["reason"]


def test_grocery_never_prices_a_food_by_a_partly_matching_name():
    result = build_grocery_list(week_plan("Mixed fruit salad", "Seasonal fruit", "Lime water", "Roti"), PRICES, None)
    fruit = next(i for c in result["categories"] for i in c["items"] if i["key"] == "fruit")
    assert fruit["unit"] == "1 bowl (150 g papaya)"            # never the cucumber "Salad" row
    assert fruit["estimated_cost_bdt"] == round(7 * 4.50)       # only "Seasonal fruit" is priced
    assert fruit["price_status"] == "partial"                   # "Mixed fruit salad" has no price
    keys = {i["key"]: i for c in result["categories"] for i in c["items"]}
    assert keys["lemon"]["estimated_cost_bdt"] == round(7 * 15) and keys["atta"]["estimated_cost_bdt"] == round(7 * 4.20)


def test_food_names_resolve_to_grocery_keys():
    assert opt.resolve_key("Fish curry") == "fish"
    assert opt.resolve_key("Boiled eggs") == "eggs"
    assert opt.resolve_key("Lentils (dal)") == "dal"
    assert opt.resolve_key("pizza") is None


# --------------------------------------------------------------------------- #
# Routes (fake database)
# --------------------------------------------------------------------------- #
class FakeDB:
    def __init__(self, allergies=None):
        self.swaps = {}
        self.next_id = 1
        self.allergies = allergies


class FakeVar:
    value = None

    def getvalue(self):
        return [self.value]


class FakeCursor:
    def __init__(self, db):
        self.db, self.res, self.rowcount = db, [], 0

    def var(self, _t):
        return FakeVar()

    def execute(self, sql, **b):
        q = " ".join(sql.split())
        self.res, self.rowcount = [], 0
        if q.startswith("SELECT price_id"):
            self.res = [(i + 1, p["item_name"], p["category"], p["unit"], p["price_bdt"], None, None, None,
                         None, None, p["market"], p["source"], p["source_url"], p["price_date"],
                         p["season"], p["season_note"], None) for i, p in enumerate(SEED_PRICES)]
        elif q.startswith("SELECT allergies"):
            self.res = [(self.db.allergies, None, "Non-vegetarian")]
        elif q.startswith("SELECT restrictions, source FROM prescription"):
            self.res = []
        elif q.startswith("SELECT swap_id, from_item"):
            self.res = [(i, s["from_item"], s["to_item"], s["ratio"], None) for i, s in self.db.swaps.items()
                        if s["patient_id"] == b["patient_id"] and s["status"] == "ACTIVE"]
        elif q.startswith("SELECT swap_id FROM meal_swap"):
            self.res = [(i,) for i, s in self.db.swaps.items() if s["patient_id"] == b["patient_id"]
                        and s["from_item"] == b["from_item"] and s["status"] == "ACTIVE"]
        elif q.startswith("INSERT INTO meal_swap"):
            sid = self.db.next_id
            self.db.next_id += 1
            self.db.swaps[sid] = {"patient_id": b["patient_id"], "from_item": b["from_item"],
                                  "to_item": b["to_item"], "ratio": b["ratio"], "status": "ACTIVE"}
            b["new_id"].value = sid
        elif q.startswith("UPDATE meal_swap SET to_item"):
            s = self.db.swaps[b["swap_id"]]
            s.update(to_item=b["to_item"], ratio=b["ratio"])
            self.rowcount = 1
        elif q.startswith("UPDATE meal_swap SET status"):
            s = self.db.swaps.get(b["swap_id"])
            if s and s["patient_id"] == b["patient_id"] and s["status"] == "ACTIVE":
                s["status"] = "UNDONE"
                self.rowcount = 1
        else:
            raise AssertionError("unexpected SQL: " + q)

    def fetchone(self):
        return self.res[0] if self.res else None

    def fetchall(self):
        return self.res

    def close(self):
        pass


class FakeConnection:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(router, "get_connection", lambda: FakeConnection(fake))
    return fake


def test_every_optimizer_route_is_patient_only():
    import inspect
    for fn in (router.read_summary, router.read_alternatives, router.read_swaps,
               router.save_swap, router.undo_swap):
        assert inspect.signature(fn).parameters["principal"].default.dependency is require_patient


def test_alternatives_route_uses_the_saved_profile(db):
    db.allergies = "Lentils"
    result = router.read_alternatives(item="Fish curry", principal=PATIENT)
    assert result["key"] == "fish" and "dal" not in alt_keys(result)


def test_unknown_food_is_404(db):
    with pytest.raises(HTTPException) as error:
        router.read_alternatives(item="pizza", principal=PATIENT)
    assert error.value.status_code == 404


def test_save_then_list_then_undo_a_swap(db):
    saved = router.save_swap(SwapIn(from_item="fish", to_item="dal"), principal=PATIENT)
    assert saved["to_key"] == "dal" and saved["portion"].startswith("2.5")
    assert [s["to_key"] for s in router.read_swaps(principal=PATIENT)["active_swaps"]] == ["dal"]
    router.undo_swap(saved["swap_id"], principal=PATIENT)
    assert router.read_swaps(principal=PATIENT)["active_swaps"] == []
    assert db.swaps[saved["swap_id"]]["status"] == "UNDONE"      # kept, never deleted


def test_saving_again_for_the_same_food_updates_the_swap(db):
    router.save_swap(SwapIn(from_item="fish", to_item="dal"), principal=PATIENT)
    router.save_swap(SwapIn(from_item="fish", to_item="chicken"), principal=PATIENT)
    assert len(db.swaps) == 1 and db.swaps[1]["to_item"] == "chicken"


def test_server_refuses_an_unsafe_swap(db):
    db.allergies = "Lentils"
    with pytest.raises(HTTPException) as error:
        router.save_swap(SwapIn(from_item="fish", to_item="dal"), principal=PATIENT)
    assert error.value.status_code == 400 and "allergy" in error.value.detail
    assert db.swaps == {}


def test_server_refuses_a_more_expensive_swap(db):
    with pytest.raises(HTTPException) as error:
        router.save_swap(SwapIn(from_item="dal", to_item="fish"), principal=PATIENT)
    assert error.value.status_code == 400


def test_a_patient_cannot_undo_another_patients_swap(db):
    saved = router.save_swap(SwapIn(from_item="fish", to_item="dal"), principal=PATIENT)
    with pytest.raises(HTTPException) as error:
        router.undo_swap(saved["swap_id"], principal=OTHER)
    assert error.value.status_code == 404
    assert db.swaps[saved["swap_id"]]["status"] == "ACTIVE"


def test_missing_swap_table_gives_a_setup_message(monkeypatch):
    import oracledb

    class Err:
        code = 942

    class Broken(FakeCursor):
        def execute(self, sql, **b):
            if "meal_swap" in sql:
                raise oracledb.Error(Err())
            return super().execute(sql, **b)

    conn = FakeConnection(FakeDB())
    conn.cursor = lambda: Broken(conn.db)
    monkeypatch.setattr(router, "get_connection", lambda: conn)
    with pytest.raises(HTTPException) as error:
        router.save_swap(SwapIn(from_item="fish", to_item="dal"), principal=PATIENT)
    assert error.value.status_code == 503 and "apply_meal_swap_scrum59" in error.value.detail
