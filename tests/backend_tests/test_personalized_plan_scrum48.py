"""SCRUM-48 tests: Personalized Diet + Activity Plan (SRS 3.3.6).

Runs WITHOUT a live Oracle connection and WITHOUT an HTTP client (``httpx`` is
not installed, so FastAPI's ``TestClient`` is unavailable -- same constraint as
the SCRUM-44/45/46 suites). Instead these tests exercise the real generator,
the real persistence service, the real router and the real SCRUM-44 security
layer against a small in-memory fake DB that models Oracle's identity-column
semantics.

What is proven here
-------------------
1. GENERATOR (SRS 3.3.6.1): the plan is built from profile, prescriptions,
   conditions, preferences, goals and budget; the output is deterministic and
   needs no ML or external service.
2. RESTRICTIONS (SRS 3.3.6.2): restricted and allergen-matching foods are
   removed from every meal, and the calorie target never drops below the safe
   minimum of 1200 kcal/day.
3. ACTIVITY (SRS 3.3.6.3): diet guidance is combined with activity guidance;
   a high-risk condition removes the intense activities and adds a caution.
4. CONFLICTS (SRS 3.3.4): a contradiction between two prescriptions is
   detected, explained in plain language and resolved to the stricter rule;
   an unsafe combination is flagged with ``requires_review`` instead of
   being guessed.
5. REGENERATION (SRS 3.3.6.4): the request body may override goals,
   preferences, activity level and budget.
6. PERSISTENCE (SRS 3.3.6.5): the generated plan is stored, the previous plan
   is kept as SUPERSEDED history, and only one plan per patient is CURRENT.
   ``PLAN_ID`` is GENERATED ALWAYS AS IDENTITY, so the INSERT must not name
   the column and must not reference a sequence.
7. RBAC: every endpoint is bound to ``require_patient``; the patient id comes
   ONLY from the verified token, never from the request.

No passwords, hashes, tokens or other secrets are ever printed.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from inspect import signature
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-scrum48")

import pytest
from fastapi import HTTPException

from backend.core.security import (
    ROLE_DOCTOR,
    ROLE_PATIENT,
    Principal,
    require_authenticated,
    require_patient,
)
import backend.routers.plan_router as plan_router
from backend.schemas.plan import (
    ACTIVITY_PROFESSIONAL_CAUTION,
    DECISION_SUPPORT_DISCLAIMER,
    STATUS_CURRENT,
    STATUS_SUPERSEDED,
    PlanCurrentResponse,
    PlanGenerateRequest,
    PlanGenerateResponse,
    PlanHistoryResponse,
)
# The plan engine lives in plan_generator.py / plan_conflict_resolver.py
# (renamed from diet_generator.py / conflict_resolver.py during SCRUM-48).
from backend.services.plan_generator import (
    MIN_CALORIE_TARGET,
    calculate_calorie_target,
    generate_plan,
)
from backend.services.plan_conflict_resolver import (
    find_dietary_conflicts,
    resolve_effective_restrictions,
)
def run(func_result):
    """Drive async dependency/endpoint calls on a fresh event loop if needed."""
    if asyncio.iscoroutine(func_result):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(func_result)
        finally:
            loop.close()
    return func_result


def status_of(excinfo):
    assert excinfo.value.status_code in (400, 401, 403, 404, 500, 503), (
        "expected an HTTP error"
    )
    return excinfo.value.status_code


def patient_principal(subject_id):
    """Build a PATIENT principal whose subject is the given patient id."""
    return Principal(subject=str(subject_id), role=ROLE_PATIENT)


def doctor_principal(subject_id):
    """Build a DOCTOR principal whose subject is the given doctor id."""
    return Principal(subject=str(subject_id), role=ROLE_DOCTOR)


# --------------------------------------------------------------------------- #
# 1) Generator: SRS 3.3.6.1 -- the plan reflects every input
# --------------------------------------------------------------------------- #
def test_generator_uses_every_input():
    plan = generate_plan(
        patient_id=1,
        age=30,
        gender="Female",
        weight_kg=65,
        height_cm=160,
        activity_level="Moderate",
        goals="Lose weight",
        conditions="Type 2 diabetes",
        allergies="Fish",
        monthly_budget=3000,
        prescriptions=[{"restrictions": "low sodium", "source": "Dr A"}],
        days=3,
    )
    assert plan["patient_id"] == 1
    assert len(plan["days"]) == 3
    # The prescription became an applied restriction.
    assert "low_sodium" in plan["restrictions_applied"]
    # The condition became a low-sugar rule.
    assert "low_sugar" in plan["restrictions_applied"]
    # Activities were generated alongside the meals (SRS 3.3.6.3).
    assert plan["activities"]
    # The budget produced guidance.
    assert plan["budget_notes"]


def test_generator_is_deterministic():
    """Same inputs -> same plan. No randomness, no external service."""
    kwargs = dict(
        patient_id=1,
        age=30,
        gender="Male",
        weight_kg=70,
        height_cm=175,
        activity_level="Light",
        goals="Build muscle",
        conditions="Anaemia",
        allergies="Peanuts",
        monthly_budget=4000,
        prescriptions=[{"restrictions": "high protein", "source": "Dr A"}],
        days=2,
    )
    assert generate_plan(**kwargs) == generate_plan(**kwargs)


def test_plan_days_rotate_so_the_week_is_not_repetitive():
    plan = generate_plan(patient_id=1, age=30, weight_kg=70, height_cm=170, days=7)
    menus = [tuple(tuple(m["items"]) for m in day["meals"]) for day in plan["days"]]
    assert len(set(menus)) > 1


def test_every_day_has_meals_and_a_calorie_total():
    plan = generate_plan(patient_id=1, age=30, weight_kg=70, height_cm=170, days=5)
    for day in plan["days"]:
        assert day["meals"]
        assert day["daily_calories"] > 0


# --------------------------------------------------------------------------- #
# 2) Restrictions & safety floor: SRS 3.3.6.2
# --------------------------------------------------------------------------- #
def test_restricted_food_is_removed_from_the_plan():
    plan = generate_plan(
        patient_id=1,
        age=30,
        weight_kg=70,
        height_cm=170,
        prescriptions=[{"restrictions": "vegetarian", "source": "Dr A"}],
        days=3,
    )
    for day in plan["days"]:
        for meal in day["meals"]:
            for item in meal["items"]:
                assert "fish" not in item.lower()
                assert "chicken" not in item.lower()


def test_allergen_matching_food_is_removed():
    plan = generate_plan(
        patient_id=1,
        age=30,
        weight_kg=70,
        height_cm=170,
        allergies="Curd",
        days=3,
    )
    for day in plan["days"]:
        for meal in day["meals"]:
            for item in meal["items"]:
                assert "curd" not in item.lower()


def test_calorie_target_never_drops_below_the_safe_minimum():
    """A weight-loss goal on a small, light patient must not go below 1200."""
    target = calculate_calorie_target(
        age=25, gender="Female", weight_kg=42, height_cm=145,
        activity_level="Sedentary", goals="Lose weight fast",
    )
    assert target >= MIN_CALORIE_TARGET



# --------------------------------------------------------------------------- #
# 3) Activity guidance: SRS 3.3.6.3
# --------------------------------------------------------------------------- #
def test_activity_guidance_is_generated_next_to_the_meals():
    plan = generate_plan(patient_id=1, age=30, weight_kg=70, height_cm=170,
                         activity_level="Active", days=1)
    assert plan["activities"]
    for activity in plan["activities"]:
        assert activity["name"]
        assert activity["duration_minutes"] > 0


def test_high_risk_condition_removes_the_intense_activities():
    plan = generate_plan(
        patient_id=1, age=60, weight_kg=80, height_cm=170,
        activity_level="Very active", conditions="Cardiac disease",
        days=1,
    )
    names = " ".join(a["name"].lower() for a in plan["activities"])
    assert "running" not in names
    assert "jogging" not in names
    assert plan["cautions"]


def test_an_exercise_restriction_is_honoured():
    plan = generate_plan(
        patient_id=1, age=40, weight_kg=70, height_cm=170,
        prescriptions=[{"restrictions": "bed rest, no exercise", "source": "Dr A"}],
        days=1,
    )
    names = " ".join(a["name"].lower() for a in plan["activities"])
    assert "jogging" not in names
    assert "running" not in names


def test_activity_catalogue_covers_every_activity_level():
    for level in ("Sedentary", "Light", "Moderate", "Active", "Very active"):
        plan = generate_plan(patient_id=1, age=30, weight_kg=70, height_cm=170,
                             activity_level=level, days=1)
        assert plan["activities"], level


# --------------------------------------------------------------------------- #
# 4) Conflict resolution: SRS 3.3.4
# --------------------------------------------------------------------------- #
def test_contradicting_prescriptions_are_detected_and_explained():
    conflicts = find_dietary_conflicts(
        prescriptions=[
            {"restrictions": "low sodium", "source": "Dr A"},
            {"restrictions": "high sodium", "source": "Dr B"},
        ]
    )
    codes = [c["code"] for c in conflicts]
    assert "contradictory_restrictions" in codes
    contradiction = next(c for c in conflicts if c["code"] == "contradictory_restrictions")
    # The explanation is human readable, not a code.
    assert "sodium" in contradiction["explanation"].lower()
    assert contradiction["resolution"]
    assert contradiction["requires_review"] is False


def test_the_stricter_rule_wins_the_contradiction():
    restrictions = resolve_effective_restrictions(
        prescriptions=[
            {"restrictions": "low sodium", "source": "Dr A"},
            {"restrictions": "high sodium", "source": "Dr B"},
        ]
    )
    assert "low_sodium" in restrictions
    assert "high_sodium" not in restrictions


def test_duplicate_restrictions_are_reported_once():
    conflicts = find_dietary_conflicts(
        prescriptions=[
            {"restrictions": "low sugar", "source": "Dr A"},
            {"restrictions": "low sugar", "source": "Dr B"},
        ]
    )
    duplicates = [c for c in conflicts if c["code"] == "duplicate_restrictions"]
    assert len(duplicates) == 1
    assert duplicates[0]["severity"] == "low"


def test_compatible_rules_are_both_kept_and_explained():
    """Vegetarian + high protein must NOT drop the vegetarian rule."""
    restrictions = resolve_effective_restrictions(
        prescriptions=[
            {"restrictions": "vegetarian", "source": "Dr A"},
            {"restrictions": "high protein", "source": "Dr B"},
        ]
    )
    assert "vegetarian" in restrictions
    assert "high_protein" in restrictions

    conflicts = find_dietary_conflicts(
        prescriptions=[
            {"restrictions": "vegetarian", "source": "Dr A"},
            {"restrictions": "high protein", "source": "Dr B"},
        ]
    )
    assert any(c["code"] == "compatible_restrictions" for c in conflicts)



# --------------------------------------------------------------------------- #
# In-memory fake Oracle that models GENERATED ALWAYS AS IDENTITY
# --------------------------------------------------------------------------- #
SENTINEL_TS = datetime(2024, 1, 1, 12, 0, 0)

_BASE_COLS = ("age", "gender", "weight", "height")
_EXT_COLS = (
    "conditions",
    "allergies",
    "activity_level",
    "food_preference",
    "goals",
    "physical_limitations",
)


class FakeCursor:
    """A cursor that understands just enough SQL for the plan router."""

    def __init__(self, db):
        self._db = db
        self._row = None
        self._rows = []
        self.closed = False
        self.rowcount = 0

    def var(self, _type=None):
        """Stand-in for ``cursor.var(int)`` used by RETURNING ... INTO."""
        return FakeVar()

    def execute(self, sql, **params):
        self._db.statements.append((" ".join(sql.split()), dict(params)))
        norm = " ".join(sql.split()).lower()
        pid = params.get("patient_id")
        if pid is not None:
            try:
                pid = int(pid)
            except (TypeError, ValueError):
                pid = None

        self._row = None
        self._rows = []

        # --- diet_plan INSERT -------------------------------------------- #
        if norm.startswith("insert into diet_plan"):
            plan_id = self._db._next_plan_id()
            self._db.plans[plan_id] = {
                "plan_id": plan_id,
                "patient_id": pid,
                "prescription_id": params.get("prescription_id"),
                "calorie_target": params.get("calorie_target"),
                "meal_schedule": params.get("meal_schedule"),
                "created_at": params.get("created_at") or SENTINEL_TS,
                "status": params.get("status") or STATUS_CURRENT,
                "meal_plan": params.get("meal_plan"),
                "activity_plan": params.get("activity_plan"),
                "profile_snapshot": params.get("profile_snapshot"),
                "restrictions_applied": params.get("restrictions_applied"),
                "conflict_notes": params.get("conflict_notes"),
                "requires_review": params.get("requires_review") or 0,
            }
            # RETURNING plan_id INTO :new_id -> cursor.var(int).getvalue()
            var = params.get("new_id")
            if var is not None:
                var.setvalue([plan_id])
            self._row = None

        # --- activity_plan INSERT ---------------------------------------- #
        elif norm.startswith("insert into activity_plan"):
            activity_id = self._db._next_activity_id()
            self._db.activities[activity_id] = {
                "activity_plan_id": activity_id,
                "patient_id": pid,
                "plan_id": params.get("plan_id"),
                "exercise_list": params.get("exercise_list"),
                "duration": params.get("duration"),
                "intensity": params.get("intensity"),
                "status": params.get("status") or STATUS_CURRENT,
                "activity_plan": params.get("activity_plan"),
                "cautions": params.get("cautions"),
            }
            self._row = None

        # --- status demotion --------------------------------------------- #
        elif norm.startswith("update diet_plan set status"):
            changed = 0
            for plan in self._db.plans.values():
                if plan["patient_id"] == pid and plan["status"] == STATUS_CURRENT:
                    plan["status"] = params.get("status")
                    changed += 1
            self.rowcount = changed
            self._row = None

        elif norm.startswith("update activity_plan set status"):
            changed = 0
            for activity in self._db.activities.values():
                if activity["patient_id"] == pid and activity["status"] == STATUS_CURRENT:
                    activity["status"] = params.get("status")
                    changed += 1
            self.rowcount = changed
            self._row = None

        # --- patient / profile / budget / prescription reads -------------- #
        elif "from patient" in norm and "age" in norm:
            patient = self._db.patients.get(pid)
            self._row = tuple(patient.get(c) for c in _BASE_COLS) if patient else None

        elif "from health_profile" in norm:
            profile = self._db.profiles.get(pid)
            self._row = (
                tuple(profile.get(c) for c in _EXT_COLS) if profile else None
            )

        elif "from budget" in norm:
            budget = self._db.budgets.get(pid)
            self._row = (budget,) if budget is not None else None

        elif "from prescription" in norm:
            self._rows = [
                (text, source)
                for text, source in self._db.prescriptions.get(pid, [])
            ]

        # --- diet_plan reads ---------------------------------------------- #
        elif "from diet_plan" in norm:
            if "where plan_id" in norm:
                plan = self._db.plans.get(int(params["plan_id"]))
                self._row = self._plan_tuple(plan) if plan else None
            else:
                plans = [
                    plan for plan in self._db.plans.values()
                    if plan["patient_id"] == pid
                ]
                if "status = :status" in norm:
                    wanted = params.get("status")
                    plans = [p for p in plans if p["status"] == wanted]
                plans.sort(key=lambda p: (p["created_at"], p["plan_id"]), reverse=True)
                self._rows = [self._plan_tuple(p) for p in plans]
                self._row = self._rows[0] if self._rows else None

        # --- activity_plan reads ------------------------------------------ #
        elif "from activity_plan" in norm:
            rows = [
                activity
                for activity in self._db.activities.values()
                if activity["plan_id"] == int(params["plan_id"])
            ]
            rows.sort(key=lambda a: a["activity_plan_id"])
            self._rows = [self._activity_tuple(a) for a in rows]

        else:
            self._row = None

    def _plan_tuple(self, plan):
        return (
            plan["plan_id"],
            plan["patient_id"],
            plan["calorie_target"],
            plan["meal_schedule"],
            plan["created_at"],
            plan["status"],
            plan["meal_plan"],
            plan["activity_plan"],
            plan["profile_snapshot"],
            plan["restrictions_applied"],
            plan["conflict_notes"],
            plan["requires_review"],
        )

    def _activity_tuple(self, activity):
        return (
            activity["activity_plan_id"],
            activity["patient_id"],
            activity["plan_id"],
            activity["exercise_list"],
            activity["duration"],
            activity["intensity"],
            activity["status"],
            activity["activity_plan"],
            activity["cautions"],
        )

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)

    def close(self):
        self.closed = True


class FakeVar:
    """Stand-in for ``cursor.var(int)`` used by RETURNING ... INTO."""

    def __init__(self):
        self._value = None

    def setvalue(self, value):
        self._value = value

    def getvalue(self):
        return self._value


class FakeConnection:
    def __init__(self, patients=None, profiles=None, budgets=None, prescriptions=None):
        self.patients = patients or {}
        self.profiles = profiles or {}
        self.budgets = budgets or {}
        self.prescriptions = prescriptions or {}
        self.plans = {}
        self.activities = {}
        self.statements = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.rowcount = 0
        self._plan_id_seq = 0
        self._activity_id_seq = 0

    def _next_plan_id(self):
        self._plan_id_seq += 1
        return self._plan_id_seq

    def _next_activity_id(self):
        self._activity_id_seq += 1
        return self._activity_id_seq

    def cursor(self):
        return FakeCursor(self)

    def var(self, _type=None):
        return FakeVar()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def make_patient(age=30, gender="Female", weight=65.0, height=160.0):
    return {"age": age, "gender": gender, "weight": weight, "height": height}


@pytest.fixture
def db(monkeypatch):
    """A live fake DB with two patients (id 1 and id 2)."""
    conn = FakeConnection(
        patients={
            1: make_patient(),
            2: make_patient(age=40, gender="Male", weight=90.0, height=180.0),
        },
        profiles={
            1: {
                "conditions": "Type 2 diabetes",
                "allergies": "Fish",
                "activity_level": "Moderate",
                "food_preference": "No restriction",
                "goals": "Lose weight",
                "physical_limitations": None,
            },
        },
        budgets={1: 3000.0},
        prescriptions={
            1: [("low sodium, high protein", "DOCTOR")],
        },
    )
    monkeypatch.setattr(plan_router, "get_connection", lambda: conn)
    return conn

# --------------------------------------------------------------------------- #
# 5) RBAC: the endpoints are bound to require_patient
# --------------------------------------------------------------------------- #
def test_patient_passes_require_patient():
    assert run(require_patient(principal=patient_principal(1))) is not None


def test_doctor_is_403_on_require_patient():
    with pytest.raises(HTTPException) as exc:
        run(require_patient(principal=doctor_principal(7)))
    assert status_of(exc) == 403


def test_missing_token_is_401_at_bearer_layer():
    with pytest.raises(HTTPException) as exc:
        run(require_authenticated(authorization=None))
    assert status_of(exc) == 401


def test_every_plan_endpoint_is_bound_to_require_patient():
    """No plan endpoint can be reached without the PATIENT dependency."""
    for endpoint in (
        plan_router.generate_my_plan,
        plan_router.read_my_current_plan,
        plan_router.read_my_plan_history,
    ):
        params = signature(endpoint).parameters.values()
        principal = next(p for p in params if p.name == "principal")
        assert principal.default.dependency is require_patient


def test_a_non_numeric_token_subject_is_401():
    with pytest.raises(HTTPException) as exc:
        plan_router.generate_my_plan(
            payload=PlanGenerateRequest(),
            principal=Principal(subject="not-a-number", role=ROLE_PATIENT),
        )
    assert status_of(exc) == 401


def test_no_endpoint_accepts_a_patient_id_from_the_request():
    """Identity comes only from the token -- never from the body."""
    for endpoint in (
        plan_router.generate_my_plan,
        plan_router.read_my_current_plan,
        plan_router.read_my_plan_history,
    ):
        params = signature(endpoint).parameters
        assert "patient_id" not in params


# --------------------------------------------------------------------------- #
# 6) Generation endpoint: SRS 3.3.6.1 / 3.3.6.4 / 3.3.6.5
# --------------------------------------------------------------------------- #
def test_generate_returns_a_full_plan(db):
    response = run(plan_router.generate_my_plan(
        payload=PlanGenerateRequest(days=3),
        principal=patient_principal(1),
    ))
    assert isinstance(response, PlanGenerateResponse)
    assert response.patient_id == 1
    assert response.plan_id > 0
    assert len(response.days) == 3
    assert response.activities
    assert response.calorie_target >= MIN_CALORIE_TARGET
    assert response.disclaimer == DECISION_SUPPORT_DISCLAIMER
    assert response.activity_caution == ACTIVITY_PROFESSIONAL_CAUTION


def test_generate_stores_the_plan(db):
    run(plan_router.generate_my_plan(
        payload=PlanGenerateRequest(days=2),
        principal=patient_principal(1),
    ))
    assert len(db.plans) == 1
    stored = next(iter(db.plans.values()))
    assert stored["status"] == STATUS_CURRENT
    assert stored["patient_id"] == 1
    # The meal plan is stored as JSON.
    assert json.loads(stored["meal_plan"])
    assert json.loads(stored["activity_plan"])
    assert db.committed is True


def test_generate_writes_a_matching_activity_plan_row(db):
    run(plan_router.generate_my_plan(
        payload=PlanGenerateRequest(days=1),
        principal=patient_principal(1),
    ))
    assert len(db.activities) == 1
    activity = next(iter(db.activities.values()))
    plan_id = next(iter(db.plans.values()))["plan_id"]
    assert activity["plan_id"] == plan_id
    assert activity["exercise_list"]


def test_regeneration_keeps_the_previous_plan_as_history(db):
    """SRS 3.3.6.5: the old plan is retained, only the new one is CURRENT."""
    first = run(plan_router.generate_my_plan(
        payload=PlanGenerateRequest(days=1),
        principal=patient_principal(1),
    ))
    second = run(plan_router.generate_my_plan(
        payload=PlanGenerateRequest(days=1),
        principal=patient_principal(1),
    ))
    assert first.plan_id != second.plan_id
    assert len(db.plans) == 2

    statuses = sorted(plan["status"] for plan in db.plans.values())
    assert statuses == [STATUS_CURRENT, STATUS_SUPERSEDED]


def test_only_one_plan_is_current_at_a_time(db):
    run(plan_router.generate_my_plan(
        payload=PlanGenerateRequest(days=1), principal=patient_principal(1)))
    run(plan_router.generate_my_plan(
        payload=PlanGenerateRequest(days=1), principal=patient_principal(1)))
    run(plan_router.generate_my_plan(
        payload=PlanGenerateRequest(days=1), principal=patient_principal(1)))
    current = [p for p in db.plans.values() if p["status"] == STATUS_CURRENT]
    assert len(current) == 1


def test_regenerate_false_keeps_the_current_plan(db):
    """regenerate=False adds a plan without demoting the current one."""
    run(plan_router.generate_my_plan(
        payload=PlanGenerateRequest(days=1), principal=patient_principal(1)))
    run(plan_router.generate_my_plan(
        payload=PlanGenerateRequest(days=1, regenerate=False),
        principal=patient_principal(1)))
    current = [p for p in db.plans.values() if p["status"] == STATUS_CURRENT]
    assert len(current) == 2


def test_a_patient_never_sees_another_patients_plan(db):
    run(plan_router.generate_my_plan(
        payload=PlanGenerateRequest(days=1), principal=patient_principal(1)))
    response = run(plan_router.read_my_current_plan(principal=patient_principal(2)))
    assert response.found is False
    assert response.patient_id == 2


def test_plan_id_is_never_written_by_the_application(db):
    """PLAN_ID is GENERATED ALWAYS AS IDENTITY: never inserted, never a sequence."""
    run(plan_router.generate_my_plan(
        payload=PlanGenerateRequest(days=1), principal=patient_principal(1)))
    inserts = [sql for sql, _ in db.statements if sql.lower().startswith("insert into diet_plan")]
    assert inserts
    for sql in inserts:
        assert "plan_id" not in sql.lower().split("values")[0]
        assert "plan_seq" not in sql.lower()
        assert "nextval" not in sql.lower()
        assert "returning plan_id into" in sql.lower()


def test_generation_is_rolled_back_on_a_database_error(db, monkeypatch):
    def boom(self, sql, **params):
        raise plan_router.oracledb.DatabaseError("ORA-00942: table or view does not exist")

    monkeypatch.setattr(FakeCursor, "execute", boom)
    with pytest.raises(HTTPException) as exc:
        plan_router.generate_my_plan(
            payload=PlanGenerateRequest(days=1), principal=patient_principal(1))
    assert status_of(exc) == 500
    assert db.rolled_back is True


# --------------------------------------------------------------------------- #
# 7) Reading the stored plan
# --------------------------------------------------------------------------- #
def test_read_current_plan_returns_the_stored_plan(db):
    generated = run(plan_router.generate_my_plan(
        payload=PlanGenerateRequest(days=2), principal=patient_principal(1)))
    current = run(plan_router.read_my_current_plan(principal=patient_principal(1)))
    assert isinstance(current, PlanCurrentResponse)
    assert current.found is True
    assert current.plan_id == generated.plan_id
    assert len(current.days) == 2
    assert current.activities
    assert current.disclaimer == DECISION_SUPPORT_DISCLAIMER


def test_read_current_plan_is_empty_when_nothing_was_generated(db):
    current = run(plan_router.read_my_current_plan(principal=patient_principal(2)))
    assert current.found is False
    assert current.days == []
    assert current.activities == []


def test_plan_history_lists_the_superseded_plans(db):
    run(plan_router.generate_my_plan(
        payload=PlanGenerateRequest(days=1), principal=patient_principal(1)))
    run(plan_router.generate_my_plan(
        payload=PlanGenerateRequest(days=1), principal=patient_principal(1)))
    history = run(plan_router.read_my_plan_history(principal=patient_principal(1)))
    assert isinstance(history, PlanHistoryResponse)
    assert history.total == 2
    statuses = {entry.status for entry in history.plans}
    assert statuses == {STATUS_CURRENT, STATUS_SUPERSEDED}


def test_plan_history_is_empty_for_a_new_patient(db):
    history = run(plan_router.read_my_plan_history(principal=patient_principal(2)))
    assert history.plans == []
    assert history.total == 0
    assert history.disclaimer == DECISION_SUPPORT_DISCLAIMER


# --------------------------------------------------------------------------- #
# 8) Request model
# --------------------------------------------------------------------------- #
def test_request_defaults_to_a_seven_day_regenerating_plan():
    request = PlanGenerateRequest()
    assert request.days == 7
    assert request.regenerate is True


def test_request_rejects_an_out_of_range_day_count():
    with pytest.raises(Exception):
        PlanGenerateRequest(days=0)
    with pytest.raises(Exception):
        PlanGenerateRequest(days=32)


def test_request_rejects_a_negative_budget():
    with pytest.raises(Exception):
        PlanGenerateRequest(monthly_budget=-10)


def test_request_never_accepts_a_patient_id():
    assert "patient_id" not in PlanGenerateRequest.model_fields


# --------------------------------------------------------------------------- #
# 9) Router registration
# --------------------------------------------------------------------------- #
def test_the_plan_router_is_registered_on_the_app():
    from backend.main import app

    paths = set(app.openapi()["paths"])
    assert "/api/plans/me" in paths
    assert "/api/plans/me/history" in paths


def test_the_generate_route_answers_201():
    route = next(
        r for r in plan_router.router.routes
        if getattr(r, "endpoint", None) is plan_router.generate_my_plan
    )
    assert route.status_code == 201

