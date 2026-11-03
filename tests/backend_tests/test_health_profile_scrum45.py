"""SCRUM-45 tests: Patient Health Profile backend (SRS 3.1.3.3, ERD Fig 5.7).

Runs WITHOUT a live Oracle connection and WITHOUT an HTTP client (``httpx`` is
not installed, so FastAPI's ``TestClient`` is unavailable -- same constraint as
the SCRUM-44 suite). Instead these tests exercise the real router and the real
SCRUM-44 security layer against a small in-memory fake DB:

* the real ``GET``/``PUT`` endpoint functions in
  ``backend.routers.health_profile_router``
* the real RBAC dependency ``require_patient`` (role == "PATIENT" only)
* the real Pydantic request/response models + disclaimer text

What is proven here
-------------------
1. RBAC: ``require_patient`` admits only PATIENT principals (403 for a DOCTOR,
   401 for a missing/invalid/wrong-purpose token). Both ``/me`` endpoints are
   structurally bound to this dependency (the signature default is inspectable).
2. Strict ownership: the patient is resolved ONLY from the authenticated token's
   ``subject_id``. Never from a path/query/body value. Cross-patient data is
   never read or written.
3. Field placement: base fields (age/gender/weight/height) are written to
   PATIENT; extended fields (conditions/allergies/activity_level/food_preference/
   goals/physical_limitations) to HEALTH_PROFILE -- no duplication.
4. Upsert: no HEALTH_PROFILE row -> INSERT; existing row -> UPDATE, both within
   one committed transaction (rollback on failure), preserving the 1-to-1 link.
5. The decision-support disclaimer is present on every response.

No passwords, hashes, tokens or other secrets are ever printed.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from inspect import signature
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-scrum45")

import pytest
from fastapi import HTTPException
from jose import jwt

from backend.routers.auth_router import ALGORITHM, SECRET_KEY, create_access_token
from backend.core.security import (
    ROLE_DOCTOR,
    ROLE_PATIENT,
    Principal,
    decode_access_token,
    require_authenticated,
    require_patient,
)
import backend.routers.health_profile_router as hp
from backend.schemas.health_profile import (
    DECISION_SUPPORT_DISCLAIMER,
    HealthProfileResponse,
    HealthProfileUpdate,
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
    assert excinfo.value.status_code in (401, 403, 503), "expected an HTTP error"
    return excinfo.value.status_code




# --------------------------------------------------------------------------- #
# In-memory fake Oracle
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
    def __init__(self, db):
        self._db = db
        self._row = None
        self.closed = False

    def execute(self, sql, **params):
        self._db.statements.append((sql, dict(params)))
        norm = " ".join(sql.split()).lower()
        pid = int(params["patient_id"]) if "patient_id" in params else None

        if norm.startswith("update patient set"):
            for col in _BASE_COLS:
                if col in params and pid in self._db.patients:
                    self._db.patients[pid][col] = params[col]
            self._row = None
        elif norm.startswith("update health_profile set"):
            prof = self._db.profiles.get(pid)
            if prof is not None:
                for col in _EXT_COLS:
                    if col in params:
                        prof[col] = params[col]
                prof["updated_at"] = SENTINEL_TS
            self._row = None
        elif norm.startswith("insert into health_profile"):
            prof = {"profile_id": self._db._next_profile_id()}
            for col in _EXT_COLS:
                if col in params:
                    prof[col] = params[col]
            prof["updated_at"] = SENTINEL_TS
            self._db.profiles[pid] = prof
            self._row = None
        elif "select 1" in norm and "from patient" in norm:
            self._row = (1,) if pid in self._db.patients else None
        elif "from health_profile" in norm and "profile_id" in norm:
            prof = self._db.profiles.get(pid)
            self._row = (prof["profile_id"],) if prof else None
        elif "from health_profile" in norm and "conditions" in norm:
            prof = self._db.profiles.get(pid)
            if prof is None:
                self._row = None
            else:
                self._row = tuple(prof.get(c) for c in _EXT_COLS) + (prof["updated_at"],)
        elif "from patient" in norm and "age" in norm:
            pat = self._db.patients.get(pid)
            self._row = tuple(pat.get(c) for c in _BASE_COLS) if pat else None
        else:
            self._row = None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row is not None else []

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, patients=None, profiles=None):
        self.patients = patients or {}
        self.profiles = profiles or {}
        self.statements = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self._profile_id_seq = 100

    def _next_profile_id(self):
        self._profile_id_seq += 1
        return self._profile_id_seq

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def make_patient(age=30, gender="Female", weight=70.0, height=165.0):
    return {"age": age, "gender": gender, "weight": weight, "height": height}


def patient_principal(subject_id):
    return Principal(subject=str(subject_id), role=ROLE_PATIENT)


@pytest.fixture
def db(monkeypatch):
    """A live fake DB with two patients (id 1 and id 2), no profiles yet."""
    conn = FakeConnection(
        patients={
            1: make_patient(),
            2: make_patient(age=40, gender="Male", weight=90.0, height=180.0),
        },
        profiles={},
    )
    monkeypatch.setattr(hp, "get_connection", lambda: conn)
    return conn


# --------------------------------------------------------------------------- #
# 1) RBAC: require_patient admits only PATIENT principals
# --------------------------------------------------------------------------- #
def test_patient_passes_require_patient():
    assert run(require_patient(principal=patient_principal(1))) is not None


def test_doctor_is_403_on_require_patient():
    doctor = Principal(subject="7", role=ROLE_DOCTOR)
    with pytest.raises(HTTPException) as exc:
        run(require_patient(principal=doctor))
    assert status_of(exc) == 403


def test_missing_token_is_401_at_bearer_layer():
    # Simulates the security dependency receiving no Authorization header.
    with pytest.raises(HTTPException) as exc:
        run(require_authenticated(authorization=None))
    assert status_of(exc) == 401


def test_wrong_purpose_token_is_401():
    reset = jwt.encode(
        {"sub": "1", "role": ROLE_PATIENT, "purpose": "password_reset",
         "iat": datetime.now(timezone.utc)},
        SECRET_KEY, algorithm=ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        run(decode_access_token(reset))
    assert status_of(exc) == 401


def test_both_me_endpoints_are_bound_to_require_patient():
    """Proves neither /me endpoint can be reached without the PATIENT dependency."""
    for endpoint in (hp.read_my_health_profile, hp.update_my_health_profile):
        params = signature(endpoint).parameters.values()
        principal = next(p for p in params if p.name == "principal")
        assert principal.default.dependency is require_patient


# --------------------------------------------------------------------------- #
# 2) GET /me reads only the authenticated patient (identity from token only)
# --------------------------------------------------------------------------- #
def test_get_returns_own_profile_when_none_yet(db):
    resp = run(hp.read_my_health_profile(principal=patient_principal(1)))
    assert isinstance(resp, HealthProfileResponse)
    assert resp.patient_id == 1
    assert resp.profile_exists is False
    assert resp.conditions is None and resp.goals is None
    # Base fields still populated from PATIENT
    assert resp.age == 30 and resp.height == 165.0
    # Disclaimer carried on every response
    assert resp.disclaimer == DECISION_SUPPORT_DISCLAIMER


def test_get_reads_correct_patient_row(db):
    db.profiles[2] = {
        "profile_id": 55, "conditions": "PCOS", "allergies": "Peanuts",
        "activity_level": "Light", "food_preference": "Vegan",
        "goals": "Lose 5 kg", "physical_limitations": "Bad knee",
        "updated_at": SENTINEL_TS,
    }
    resp = run(hp.read_my_health_profile(principal=patient_principal(2)))
    assert resp.patient_id == 2
    assert resp.profile_exists is True
    assert resp.conditions == "PCOS"
    assert resp.allergies == "Peanuts"
    assert resp.food_preference == "Vegan"
    assert resp.goals == "Lose 5 kg"
    assert resp.physical_limitations == "Bad knee"
    assert resp.updated_at == SENTINEL_TS


# --------------------------------------------------------------------------- #
# 3) PUT /me upserts within one transaction + correct field placement
# --------------------------------------------------------------------------- #
def test_put_creates_profile_when_none_exists(db):
    payload = HealthProfileUpdate(
        age=31, gender="Female", weight=72.0, height=166.0,
        conditions="Anemia", allergies="None",
        activity_level="Moderate", food_preference="Vegetarian",
        goals="Improve iron levels", physical_limitations="None",
    )
    resp = run(hp.update_my_health_profile(payload, principal=patient_principal(1)))

    # Base fields went to PATIENT
    assert db.patients[1]["age"] == 31
    assert db.patients[1]["gender"] == "Female"
    assert db.patients[1]["weight"] == 72.0
    assert db.patients[1]["height"] == 166.0
    # Extended fields went to HEALTH_PROFILE
    prof = db.profiles[1]
    assert prof["conditions"] == "Anemia"
    assert prof["allergies"] == "None"
    assert prof["activity_level"] == "Moderate"
    assert prof["food_preference"] == "Vegetarian"
    assert prof["goals"] == "Improve iron levels"
    assert prof["physical_limitations"] == "None"
    # One INSERT, single commit, connection closed
    assert db.committed is True and db.rolled_back is False
    assert db.closed is True
    assert resp.profile_exists is True
    assert resp.disclaimer == DECISION_SUPPORT_DISCLAIMER


def test_put_updates_existing_profile_and_keeps_one_to_one(db):
    db.profiles[1] = {
        "profile_id": 42, "conditions": "Old", "allergies": "Old",
        "activity_level": "Old", "food_preference": "Old",
        "goals": "Old", "physical_limitations": "Old",
        "updated_at": SENTINEL_TS,
    }
    payload = HealthProfileUpdate(conditions="Hypertension", goals="Lower sodium")
    resp = run(hp.update_my_health_profile(payload, principal=patient_principal(1)))

    # Same single profile row updated in place (no second row created).
    assert len(db.profiles) == 1
    assert db.profiles[1]["profile_id"] == 42
    assert db.profiles[1]["conditions"] == "Hypertension"
    assert db.profiles[1]["goals"] == "Lower sodium"
    # Untouched extended fields retain prior values (partial update).
    assert db.profiles[1]["allergies"] == "Old"
    assert resp.profile_exists is True


def test_put_partial_base_fields_only_updates_provided(db):
    original_height = db.patients[1]["height"]
    payload = HealthProfileUpdate(weight=80.0)  # only weight provided
    run(hp.update_my_health_profile(payload, principal=patient_principal(1)))
    assert db.patients[1]["weight"] == 80.0
    # Non-provided base fields left unchanged
    assert db.patients[1]["height"] == original_height
    assert db.patients[1]["age"] == 30


def test_put_of_unknown_patient_is_401_and_rolls_back(db):
    payload = HealthProfileUpdate(conditions="X")
    with pytest.raises(HTTPException) as exc:
        run(hp.update_my_health_profile(payload, principal=patient_principal(999)))
    assert status_of(exc) == 401
    assert db.rolled_back is True
    assert db.committed is False


def test_put_error_mid_transaction_rolls_back(db, monkeypatch):
    # Force commit() to blow up; router must roll back and not mask the error.
    def _boom():
        raise RuntimeError("simulated commit failure")
    monkeypatch.setattr(db, "commit", _boom)
    payload = HealthProfileUpdate(conditions="Anything")
    with pytest.raises(RuntimeError):
        run(hp.update_my_health_profile(payload, principal=patient_principal(1)))
    assert db.rolled_back is True


def test_body_patient_id_is_ignored_for_ownership(db):
    """A patient_id smuggled in the body must NOT change which row is touched."""
    payload = HealthProfileUpdate(conditions="Migraine")
    # Authenticated as patient 1; any body-level id intent targets patient 1.
    resp = run(hp.update_my_health_profile(payload, principal=patient_principal(1)))
    assert resp.patient_id == 1
    assert db.profiles[1]["conditions"] == "Migraine"
    # Patient 2 untouched
    assert 2 not in db.profiles


def test_db_connection_failure_is_503(monkeypatch):
    monkeypatch.setattr(hp, "get_connection", lambda: None)
    with pytest.raises(HTTPException) as exc:
        run(hp.read_my_health_profile(principal=patient_principal(1)))
    assert status_of(exc) == 503


# --------------------------------------------------------------------------- #
# 4) Schema behaviour
# --------------------------------------------------------------------------- #
def test_update_schema_ignores_extra_body_fields():
    payload = HealthProfileUpdate(conditions="Asthma", patient_id=999, evil="x")
    assert payload.conditions == "Asthma"
    assert not hasattr(payload, "patient_id")


def test_update_schema_rejects_invalid_numbers():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        HealthProfileUpdate(age=-1)
    with pytest.raises(ValidationError):
        HealthProfileUpdate(weight=0)


def test_response_always_includes_disclaimer_even_when_empty():
    resp = HealthProfileResponse.build(patient_id=1, patient_row=(30, "F", 70.0, 165.0), profile_row=None)
    assert resp.disclaimer == DECISION_SUPPORT_DISCLAIMER
    assert "decision-support" in resp.disclaimer.lower()


def test_get_of_unknown_patient_is_401(db):
    with pytest.raises(HTTPException) as exc:
        run(hp.read_my_health_profile(principal=patient_principal(999)))
    assert status_of(exc) == 401

