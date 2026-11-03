"""SCRUM-49 tests: today's meal plan and plan acceptance (SRS 3.1.3.8).

Runs WITHOUT a live Oracle connection. A small in-memory fake answers the
DIET_PLAN queries so ownership, acceptance and the review rule can be checked.
"""

import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")

import pytest
from fastapi import HTTPException

from backend.core.security import Principal
from backend.routers import meal_plan_router as router
from backend.schemas.plan import dumps_plan_part

CREATED = datetime(2026, 10, 20, 9, 0)
DAYS = [{"day": i + 1, "meals": [{"meal_type": "Breakfast", "time": "08:00", "items": [f"item{i}"],
                                  "calories": 500}], "daily_calories": 500} for i in range(7)]


class FakeDB:
    def __init__(self):
        # plan_id -> stored plan
        self.plans = {
            1: dict(patient=5, status="CURRENT", review=0, accepted=None),
            2: dict(patient=5, status="SUPERSEDED", review=0, accepted=datetime(2026, 10, 1)),
        }
        self.commits = 0
        self.rollbacks = 0

    def row(self, plan_id):
        p = self.plans[plan_id]
        return (plan_id, p["patient"], 1800, "", CREATED, p["status"], dumps_plan_part(DAYS),
                "[]", "{}", "[]", "[]", p["review"])


class FakeCursor:
    def __init__(self, db):
        self.db, self.res, self.rowcount = db, [], 0

    def execute(self, sql, **b):
        q = " ".join(sql.split())
        self.res = []
        if q.startswith("SELECT plan_id, patient_id") and "status = :status" in q:
            self.res = [self.db.row(i) for i, p in self.db.plans.items()
                        if p["patient"] == b["patient_id"] and p["status"] == b["status"]]
        elif q.startswith("SELECT plan_id, patient_id") and "WHERE plan_id" in q:
            self.res = [self.db.row(b["plan_id"])] if b["plan_id"] in self.db.plans else []
        elif q.startswith("SELECT accepted_at"):
            p = self.db.plans.get(b["plan_id"])
            self.res = [(p["accepted"],)] if p and p["patient"] == b["patient_id"] else []
        elif q.startswith("SELECT plan_id, accepted_at"):
            self.res = sorted([(i, p["accepted"]) for i, p in self.db.plans.items()
                               if p["patient"] == b["patient_id"] and p["accepted"]],
                              key=lambda t: t[1], reverse=True)
        elif q.startswith("UPDATE diet_plan SET accepted_at"):
            p = self.db.plans.get(b["plan_id"])
            self.rowcount = 0
            if p and p["patient"] == b["patient_id"]:
                p["accepted"] = b["accepted_at"]
                self.rowcount = 1
        elif "FROM activity_plan" in q:
            self.res = []
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
        self.db.rollbacks += 1

    def close(self):
        pass


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(router, "get_connection", lambda: FakeConnection(fake))
    return fake


PATIENT = Principal(subject="5", role="PATIENT")
OTHER = Principal(subject="9", role="PATIENT")


def test_today_index_day_one_is_the_creation_day():
    assert router.today_index(CREATED, date(2026, 10, 20), 7) == 0


def test_today_index_moves_one_day_at_a_time():
    assert router.today_index(CREATED, date(2026, 10, 24), 7) == 4


def test_today_index_repeats_after_the_last_day():
    assert router.today_index(CREATED, date(2026, 10, 27), 7) == 0


def test_today_index_never_goes_negative():
    assert router.today_index(CREATED, date(2026, 10, 19), 7) == 0


def test_today_returns_the_current_plan_and_acceptance_state(db):
    today = router.read_today(client_date=None, principal=PATIENT)
    assert today["found"] is True
    assert today["accepted"] is False
    assert today["accepted_plan_id"] == 2  # an older plan was accepted before
    assert today["can_accept"] is True


def test_accept_marks_the_current_plan_and_commits(db):
    result = router.accept_current_plan(principal=PATIENT)
    assert result["accepted"] is True and result["plan_id"] == 1
    assert db.commits == 1
    assert router.read_accepted_plan(principal=PATIENT)["plan"]["plan_id"] == 1


def test_plan_that_needs_review_cannot_be_accepted(db):
    db.plans[1]["review"] = 1
    with pytest.raises(HTTPException) as error:
        router.accept_current_plan(principal=PATIENT)
    assert error.value.status_code == 409
    assert db.commits == 0
    assert router.read_today(client_date=None, principal=PATIENT)["can_accept"] is False


def test_patient_without_a_plan_gets_404_on_accept(db):
    with pytest.raises(HTTPException) as error:
        router.accept_current_plan(principal=OTHER)
    assert error.value.status_code == 404


def test_a_patient_never_sees_another_patients_plan(db):
    assert router.read_today(client_date=None, principal=OTHER)["found"] is False
    assert router.read_accepted_plan(principal=OTHER)["found"] is False
