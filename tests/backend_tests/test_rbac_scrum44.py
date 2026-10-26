"""SCRUM-44 tests: role-based access control on the FastAPI backend.

Runs WITHOUT a live Oracle connection and WITHOUT an HTTP client (``httpx`` is
not installed, so FastAPI's TestClient is unavailable). Instead these tests
exercise the real security layer directly:

* real JWT signing and verification (create_access_token / decode_access_token)
* the real authorization dependencies (require_role / require_self_or_doctor)
* the real doctor-login query path against a fake connection

Status code contract asserted here:
  401 -- missing / malformed / expired / bad-signature / wrong-purpose token,
         or an unknown/blank principal.
  403 -- authenticated but wrong role, or a PATIENT reading another patient.

No passwords, hashes, tokens or other secrets are ever printed.
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-scrum44")

import pytest
from fastapi import HTTPException
from jose import jwt

from backend.routers.auth_router import ALGORITHM, SECRET_KEY, create_access_token
from backend.core.security import (
    ROLE_DOCTOR,
    ROLE_PATIENT,
    Principal,
    decode_access_token,
    require_doctor,
    require_patient,
    require_self_or_doctor,
)


def run(func_result):
    """Support both sync endpoints and async ones without an httpx client.

    ``doctor_login`` is a plain synchronous function that returns a dict, so
    there is nothing to await. Async endpoints are driven on a fresh event
    loop. Either way the caller gets the return value (or the raised
    HTTPException propagates unchanged).
    """
    if asyncio.iscoroutine(func_result):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(func_result)
        finally:
            loop.close()
    return func_result


def status_of(excinfo):
    assert excinfo.value.status_code in (401, 403), "expected an HTTP error"
    return excinfo.value.status_code


# ----------------------------- token happy path ---------------------------- #
def test_patient_token_decodes_to_patient_principal():
    token = create_access_token({"sub": "42", "role": ROLE_PATIENT, "typ": "access"})
    principal = decode_access_token(token)
    assert isinstance(principal, Principal)
    assert principal.subject == "42"
    assert principal.role == ROLE_PATIENT
    assert principal.is_patient and not principal.is_doctor


def test_doctor_token_decodes_to_doctor_principal():
    token = create_access_token({"sub": "7", "role": ROLE_DOCTOR, "typ": "access"})
    principal = decode_access_token(token)
    assert principal.subject == "7"
    assert principal.role == ROLE_DOCTOR
    assert principal.is_doctor and not principal.is_patient


# ------------------------------- 401 cases --------------------------------- #
def test_garbage_token_is_401():
    with pytest.raises(HTTPException) as exc:
        decode_access_token("not-a-real-token")
    assert status_of(exc) == 401


def test_token_with_wrong_signature_is_401():
    forged = jwt.encode(
        {"sub": "42", "role": ROLE_PATIENT, "typ": "access", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        "an-attacker-secret",
        algorithm=ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        decode_access_token(forged)
    assert status_of(exc) == 401


def test_expired_token_is_401():
    expired = jwt.encode(
        {"sub": "42", "role": ROLE_PATIENT, "typ": "access", "exp": datetime.now(timezone.utc) - timedelta(minutes=5)},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        decode_access_token(expired)
    assert status_of(exc) == 401


def test_password_reset_token_cannot_be_used_as_access_token():
    reset = jwt.encode(
        {"sub": "42", "role": ROLE_PATIENT, "purpose": "password_reset", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        decode_access_token(reset)
    assert status_of(exc) == 401


def test_token_missing_role_is_401():
    no_role = jwt.encode(
        {"sub": "42", "typ": "access", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        decode_access_token(no_role)
    assert status_of(exc) == 401


def test_token_with_unknown_role_is_401():
    # SCRUM-61 made ADMIN a real role, so an unknown role is now e.g. SUPERUSER.
    bad_role = create_access_token({"sub": "42", "role": "SUPERUSER", "typ": "access"})
    with pytest.raises(HTTPException) as exc:
        decode_access_token(bad_role)
    assert status_of(exc) == 401


# ------------------------------- 403 cases --------------------------------- #
def test_patient_cannot_use_doctor_only_dependency():
    patient = Principal(subject="42", role=ROLE_PATIENT)
    with pytest.raises(HTTPException) as exc:
        run(require_doctor(principal=patient))
    assert status_of(exc) == 403


def test_doctor_cannot_use_patient_only_dependency():
    doctor = Principal(subject="7", role=ROLE_DOCTOR)
    with pytest.raises(HTTPException) as exc:
        run(require_patient(principal=doctor))
    assert status_of(exc) == 403


def test_matching_role_passes():
    patient = Principal(subject="42", role=ROLE_PATIENT)
    assert run(require_patient(principal=patient)) is patient


# ------------------- ownership: id-collision + cross-patient ---------------- #
def test_patient_cannot_read_another_patients_data():
    attacker = Principal(subject="42", role=ROLE_PATIENT)
    with pytest.raises(HTTPException) as exc:
        run(require_self_or_doctor(patient_id=99, principal=attacker))
    assert status_of(exc) == 403


def test_patient_can_read_own_data():
    owner = Principal(subject="42", role=ROLE_PATIENT)
    assert run(require_self_or_doctor(patient_id=42, principal=owner)) is owner


def test_doctor_id_does_not_satisfy_patient_ownership():
    # CRITICAL: PATIENT_ID and DOCTOR_ID share the numeric space. A doctor with
    # id 42 must NOT pass a patient-42 ownership check; only the DOCTOR branch
    # lets a doctor through, and it returns the doctor principal unchanged.
    doctor = Principal(subject="42", role=ROLE_DOCTOR)
    result = run(require_self_or_doctor(patient_id=42, principal=doctor))
    assert result.role == ROLE_DOCTOR  # admitted as a doctor, not as the patient


# --------------------------- doctor login (fake DB) ------------------------- #
class FakeCursor:
    def __init__(self, row):
        self._row = row
        self.closed = False

    def execute(self, sql, **params):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self._row

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, row):
        self._row = row
        self.closed = False

    def cursor(self):
        return FakeCursor(self._row)

    def close(self):
        self.closed = True


def make_doctor_row(password, email="doc@example.com"):
    from werkzeug.security import generate_password_hash

    return (7, "Dr. Test", email, generate_password_hash(password) if password else None)


def test_doctor_login_success_returns_doctor_access_token(monkeypatch):
    import backend.routers.doctor_router as dr

    row = make_doctor_row("s3cret-pass")
    monkeypatch.setattr(dr, "get_connection", lambda: FakeConnection(row))

    result = run(dr.doctor_login(dr.DoctorLoginRequest(email="doc@example.com", password="s3cret-pass")))
    assert result["user"]["role"] == "DOCTOR"
    assert result["user"]["doctor_id"] == 7

    principal = decode_access_token(result["access_token"])
    assert principal.role == ROLE_DOCTOR and principal.subject == "7"


def test_doctor_login_wrong_password_is_401(monkeypatch):
    import backend.routers.doctor_router as dr

    row = make_doctor_row("correct-pass")
    monkeypatch.setattr(dr, "get_connection", lambda: FakeConnection(row))

    with pytest.raises(HTTPException) as exc:
        run(dr.doctor_login(dr.DoctorLoginRequest(email="doc@example.com", password="wrong")))
    assert status_of(exc) == 401


def test_doctor_login_unknown_account_is_401(monkeypatch):
    import backend.routers.doctor_router as dr

    monkeypatch.setattr(dr, "get_connection", lambda: FakeConnection(None))
    with pytest.raises(HTTPException) as exc:
        run(dr.doctor_login(dr.DoctorLoginRequest(email="nobody@example.com", password="x")))
    assert status_of(exc) == 401


def test_doctor_without_credential_cannot_login(monkeypatch):
    # Legacy DOCTOR rows have password_hash = NULL and must be rejected.
    import backend.routers.doctor_router as dr

    row = make_doctor_row(None)
    monkeypatch.setattr(dr, "get_connection", lambda: FakeConnection(row))

    with pytest.raises(HTTPException) as exc:
        run(dr.doctor_login(dr.DoctorLoginRequest(email="doc@example.com", password="anything")))
    assert status_of(exc) == 401
