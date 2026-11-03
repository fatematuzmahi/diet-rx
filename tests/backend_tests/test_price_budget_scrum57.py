"""SCRUM-57 tests: price data + budget (SRS 3.3.3.1).

Runs WITHOUT a live Oracle connection. Pure rules are called directly and the
routes are driven with a small in-memory fake of FOOD_PRICE and BUDGET.
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.core.security import Principal, require_doctor, require_doctor_or_admin, require_patient
from backend.routers import price_router as router
from backend.schemas.price import BudgetIn, PriceIn
from backend.services.grocery_service import build_grocery_list
from backend.services.price_service import SEED_PRICES, is_stale, normalise_budget, serving_price

PATIENT = Principal(subject="5", role="PATIENT")
DOCTOR = Principal(subject="7", role="DOCTOR")


# --------------------------------------------------------------------------- #
# Seeded data
# --------------------------------------------------------------------------- #
def test_every_seeded_price_has_a_source_link_and_date():
    for row in SEED_PRICES:
        assert row["source"] and row["source_url"].startswith("https://")
        assert isinstance(row["price_date"], date)
        assert row["price_bdt"] > 0


def test_serving_price_uses_the_midpoint_of_the_market_range():
    assert serving_price(350, 360, 0.1) == 35.5      # rui fish, 100 g
    egg = next(r for r in SEED_PRICES if r["item_name"] == "Egg")
    assert egg["price_bdt"] == 12.5                   # Tk150 a dozen


def test_seeded_names_are_unique_per_unit_and_market():
    keys = [(r["item_name"].upper(), r["unit"].upper(), r["market"].upper()) for r in SEED_PRICES]
    assert len(keys) == len(set(keys))


def test_seeded_prices_match_the_plan_items_used_by_the_grocery_list():
    days = [{"meals": [{"items": ["Rice", "Dal", "Boiled egg", "Mixed vegetables"]}]}]
    result = build_grocery_list(days, SEED_PRICES, None)
    assert result["summary"]["total_bdt"] is not None
    assert result["summary"]["coverage_percent"] == 100


def _grocery_line(result, key):
    return next(i for c in result["categories"] for i in c["items"] if i["key"] == key)


def test_each_ingredient_of_a_dish_gets_its_own_price():
    days = [{"meals": [{"items": ["Oats porridge with milk", "Khichuri"]}]}]
    result = build_grocery_list(days, SEED_PRICES, None)
    oats, milk = _grocery_line(result, "oats"), _grocery_line(result, "milk")
    assert oats["estimated_cost_bdt"] is None and oats["price_status"] == "missing"  # never the milk price
    assert milk["estimated_cost_bdt"] == 22 and milk["unit"] == "1 glass (200 ml)"
    assert _grocery_line(result, "rice")["price_status"] == "ok"                     # Khichuri = rice + dal
    assert _grocery_line(result, "dal")["price_status"] == "ok"


def test_old_price_is_marked_stale():
    assert is_stale(date.today() - timedelta(days=31))
    assert not is_stale(date.today() - timedelta(days=3))


# --------------------------------------------------------------------------- #
# Budget rules
# --------------------------------------------------------------------------- #
def test_daily_weekly_monthly_and_plan_budgets_normalise_the_same_way():
    assert normalise_budget("DAILY", 200)["per_month"] == 6000
    assert normalise_budget("WEEKLY", 1400)["per_day"] == 200
    assert normalise_budget("MONTHLY", 6000)["per_week"] == 1400
    assert normalise_budget("PLAN", 1400, 7)["per_day"] == 200


def test_budget_mode_is_case_insensitive():
    assert BudgetIn(mode="weekly", amount=1400).mode == "WEEKLY"


def test_budget_must_be_positive():
    with pytest.raises(ValidationError):
        BudgetIn(mode="DAILY", amount=0)


def test_plan_budget_needs_plan_days():
    with pytest.raises(ValidationError):
        BudgetIn(mode="PLAN", amount=1400)
    with pytest.raises(ValidationError):
        BudgetIn(mode="PLAN", amount=1400, plan_days=40)


def test_unknown_budget_mode_is_rejected():
    with pytest.raises(ValidationError):
        BudgetIn(mode="YEARLY", amount=1000)


def test_price_rejects_zero_future_date_and_plain_http_source():
    base = dict(item_name="Rice", unit="1 plate", source="TBS")
    with pytest.raises(ValidationError):
        PriceIn(**base, price_bdt=0)
    with pytest.raises(ValidationError):
        PriceIn(**base, price_bdt=4, price_date=date.today() + timedelta(days=1))
    with pytest.raises(ValidationError):
        PriceIn(**base, price_bdt=4, source_url="http://example.com")


def test_price_needs_a_source():
    with pytest.raises(ValidationError):
        PriceIn(item_name="Rice", unit="1 plate", price_bdt=4, source="  ")


# --------------------------------------------------------------------------- #
# Routes (fake database)
# --------------------------------------------------------------------------- #
class FakeDB:
    def __init__(self):
        self.prices = {}
        self.budgets = {}
        self.next_price = 1
        self.next_budget = 1
        self.commits = 0


class FakeVar:
    def __init__(self):
        self.value = None

    def getvalue(self):
        return [self.value]


class FakeCursor:
    def __init__(self, db):
        self.db, self.res, self.rowcount = db, [], 0

    def var(self, _type):
        return FakeVar()

    def _price_row(self, pid):
        p = self.db.prices[pid]
        return (pid, p["item_name"], p["category"], p["unit"], p["price_bdt"], None, None, None, None, None,
                p["market"], p["source"], p["source_url"], p["price_date"], p["season"], p["season_note"], None)

    def execute(self, sql, **b):
        q = " ".join(sql.split())
        self.res, self.rowcount = [], 0
        if q.startswith("SELECT price_id FROM food_price"):
            self.res = [(i,) for i, p in self.db.prices.items()
                        if p["item_name"].upper() == b["item_name"].upper() and p["unit"].upper() == b["unit"].upper()
                        and (p["market"] or "-").upper() == (b["market"] or "-").upper() and i != b.get("price_id")]
        elif q.startswith("SELECT price_id, item_name") and "WHERE price_id" in q:
            self.res = [self._price_row(b["price_id"])] if b["price_id"] in self.db.prices else []
        elif q.startswith("SELECT price_id, item_name"):
            self.res = [self._price_row(i) for i in self.db.prices]
        elif q.startswith("INSERT INTO food_price"):
            pid = self.db.next_price
            self.db.next_price += 1
            self.db.prices[pid] = {k: b[k] for k in ("item_name", "category", "unit", "price_bdt", "market",
                                                     "source", "source_url", "price_date", "season", "season_note")}
            b["new_id"].value = pid
            self.rowcount = 1
        elif q.startswith("UPDATE food_price"):
            if b["price_id"] in self.db.prices:
                self.db.prices[b["price_id"]].update({k: b[k] for k in ("item_name", "unit", "price_bdt")})
                self.rowcount = 1
        elif q.startswith("SELECT budget_id"):
            rows = [(i, r["monthly_budget"], r["preferred_market"], r["budget_mode"], r["budget_amount"],
                     r["plan_days"], r["updated_at"]) for i, r in self.db.budgets.items()
                    if r["patient_id"] == b["patient_id"]]
            self.res = sorted(rows, reverse=True)
        elif q.startswith("INSERT INTO budget"):
            self.db.budgets[self.db.next_budget] = dict(b)
            self.db.next_budget += 1
        elif q.startswith("UPDATE budget"):
            row = self.db.budgets.get(b["budget_id"])
            if row and row["patient_id"] == b["patient_id"]:
                row.update({k: v for k, v in b.items() if k != "budget_id"})
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
        self.db.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(router, "get_connection", lambda: FakeConnection(fake))
    return fake


def price_body(**overrides):
    data = dict(item_name="Rice", category="Grains", unit="1 plate (75 g raw)", price_bdt=3.94,
                source="The Business Standard", source_url="https://www.tbsnews.net/x",
                price_date=date.today())
    data.update(overrides)
    return PriceIn(**data)


def test_price_writes_are_limited_to_the_data_maintainer_role():
    import inspect
    for fn in (router.add_price, router.update_price):
        assert inspect.signature(fn).parameters["principal"].default.dependency is require_doctor_or_admin  # SCRUM-61


def test_budget_routes_are_patient_only():
    import inspect
    for fn in (router.read_my_budget, router.save_my_budget):
        assert inspect.signature(fn).parameters["principal"].default.dependency is require_patient


def test_add_price_then_list_it(db):
    created = router.add_price(price_body(), principal=DOCTOR)
    assert created["price_id"] == 1 and created["stale"] is False
    listed = router.list_prices(search=None, category=None, principal=PATIENT)
    assert listed["total"] == 1 and listed["disclaimer"]
    assert db.prices[1]["source"] == "The Business Standard"


def test_duplicate_price_is_409(db):
    router.add_price(price_body(), principal=DOCTOR)
    with pytest.raises(HTTPException) as error:
        router.add_price(price_body(item_name="rice"), principal=DOCTOR)
    assert error.value.status_code == 409


def test_update_unknown_price_is_404(db):
    with pytest.raises(HTTPException) as error:
        router.update_price(99, price_body(), principal=DOCTOR)
    assert error.value.status_code == 404


def test_no_budget_yet_is_reported_not_guessed(db):
    budget = router.read_my_budget(principal=PATIENT)
    assert budget["found"] is False and budget["per_month"] is None


def test_save_budget_stores_mode_and_monthly_equivalent(db):
    saved = router.save_my_budget(BudgetIn(mode="WEEKLY", amount=1400, preferred_market="Karwan Bazar"),
                                  principal=PATIENT)
    assert saved["found"] and saved["mode"] == "WEEKLY" and saved["per_month"] == 6000
    stored = next(iter(db.budgets.values()))
    assert stored["monthly_budget"] == 6000          # SCRUM-48/60 keep reading MONTHLY_BUDGET


def test_saving_again_updates_the_same_row(db):
    router.save_my_budget(BudgetIn(mode="DAILY", amount=200), principal=PATIENT)
    router.save_my_budget(BudgetIn(mode="DAILY", amount=250), principal=PATIENT)
    assert len(db.budgets) == 1
    assert router.read_my_budget(principal=PATIENT)["amount"] == 250


def test_a_patient_never_sees_another_patients_budget(db):
    router.save_my_budget(BudgetIn(mode="DAILY", amount=200), principal=PATIENT)
    other = router.read_my_budget(principal=Principal(subject="9", role="PATIENT"))
    assert other["found"] is False


def test_old_monthly_budget_is_still_understood(db):
    db.budgets[1] = dict(patient_id=5, monthly_budget=3000, preferred_market=None, budget_mode=None,
                         budget_amount=None, plan_days=None, updated_at=None)
    budget = router.read_my_budget(principal=PATIENT)
    assert budget["mode"] == "MONTHLY" and budget["per_day"] == 100


def test_missing_table_gives_a_setup_message(monkeypatch):
    import oracledb

    class Err:
        code = 942

    def broken():
        conn = FakeConnection(FakeDB())

        class Cur(FakeCursor):
            def execute(self, sql, **b):
                raise oracledb.Error(Err())

        conn.cursor = lambda: Cur(conn.db)
        return conn

    monkeypatch.setattr(router, "get_connection", broken)
    with pytest.raises(HTTPException) as error:
        router.list_prices(search=None, category=None, principal=PATIENT)
    assert error.value.status_code == 503
