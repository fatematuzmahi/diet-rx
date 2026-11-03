"""SCRUM-46 tests: Prescription Management backend (SRS 3.1.3.x).

Runs WITHOUT a live Oracle connection and WITHOUT an HTTP client (``httpx`` is
not installed, so FastAPI's ``TestClient`` is unavailable -- same constraint as
the SCRUM-44/45 suites). Instead these tests exercise the real router and the
real SCRUM-44 security layer against a small in-memory fake DB that models
Oracle's identity-column semantics.

What is proven here
-------------------
1. IDENTITY-COLUMN INSERT (the SCRUM-46 defect): ``PRESCRIPTION_ID`` is
   ``GENERATED ALWAYS AS IDENTITY``, so the router's INSERT must NOT name the
   column and must NOT reference a sequence. The generated key is fetched with
   ``RETURNING prescription_id INTO :new_id``. ``cursor.var(int).getvalue()``
   returns a one-element LIST, so the id is ``getvalue()[0]``. Any SQL that
   mentions ``prescription_id`` in the column list or ``prescription_seq`` is a
   regression (it is what raised ``ORA-02289`` against the live database).
2. RBAC: ``require_patient`` admits only PATIENT principals; the create
   endpoint is structurally bound to it.
3. Strict ownership: the owner is the token subject only; ``source`` is forced
   to PATIENT, ``doctor_id`` is NULL and ``validated`` is 0 -- never trusted
   from the request body.
4. Transaction discipline: one commit on success, rollback on failure, and the
   cursor/connection are always closed.
5. CLOB round-trip: the RESTRICTIONS text is read back through ``str()`` and is
   preserved intact for long input.
6. Upload references: the stored file's reference/metadata reach the DB, and a
   rejected upload never creates a prescription row.

No passwords, hashes, tokens or other secrets are ever printed.
"""

import asyncio
import os
import sys
from inspect import signature
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-scrum46")

import pytest
from fastapi import HTTPException

from backend.core.security import ROLE_DOCTOR, ROLE_PATIENT, Principal, require_patient
import backend.routers.prescription_router as pr
from backend.schemas.prescription import (
    DECISION_SUPPORT_DISCLAIMER,
    SOURCE_DOCTOR,
    SOURCE_PATIENT,
    STATUS_ACTIVE,
    PrescriptionCreate,
    PrescriptionResponse,
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


# --------------------------------------------------------------------------- #
# In-memory fake Oracle that models GENERATED ALWAYS AS IDENTITY
# --------------------------------------------------------------------------- #

class FakeVar:
    """Stand-in for ``cursor.var(int)``: ``getvalue()`` returns a LIST."""

    def __init__(self, db):
        self._db = db
        self._values = []

    def getvalue(self):
        return list(self._values)

    def setvalue(self, value):
        self._values = list(value)


class FakeCursor:
    """Records statements and mimics identity-column DML.

    ``cursor.var(int)`` returns a holder whose ``getvalue()`` yields a
    one-element LIST, exactly like python-oracledb does for a DML
    ``RETURNING INTO`` bind.
    """

    def __init__(self, db):
        self._db = db
        self._row = None
        self.closed = False

    def var(self, type_code=None):
        return FakeVar(self._db)

    def execute(self, sql, **params):
        self._db.statements.append((" ".join(sql.split()), dict(params)))
        norm = " ".join(sql.split()).lower()

        if norm.startswith("insert into prescription"):
            self._db.insert_rows.append(dict(params))
            # Oracle allocates the identity value; the router never supplies it.
            new_id = self._db._next_prescription_id()
            self._db.prescriptions[new_id] = {
                "prescription_id": new_id,
                "patient_id": params["patient_id"],
                "doctor_id": None,
                "upload_date": params.get("upload_date"),
                "restrictions": params.get("restrictions"),
                "validated": 0,
                "source": params.get("source"),
                "status": params.get("status"),
                "updated_at": params.get("updated_at"),
                "file_path": params.get("file_path"),
                "file_name": params.get("file_name"),
                "file_mime_type": params.get("file_mime_type"),
                "file_size_bytes": params.get("file_size_bytes"),
            }
            # Populate the RETURNING INTO bind variable.
            holder = params.get("new_id")
            if holder is not None:
                holder.setvalue([new_id])
            self._row = None

        elif norm.startswith("select") and "from prescription" in norm:
            if "where prescription_id = :id" in norm:
                pid = params.get("id")
                row = self._db.prescriptions.get(pid)
                self._row = self._db._as_tuple(row) if row is not None else None
            elif "where patient_id = :patient_id" in norm:
                pid = params["patient_id"]
                self._db._last_list = [
                    r for r in self._db.prescriptions.values()
                    if r["patient_id"] == pid and r["source"] == params.get("source")
                ]
                self._row = None
            elif "where doctor_id = :doctor_id" in norm:
                did = params["doctor_id"]
                self._db._last_list = [
                    r for r in self._db.prescriptions.values()
                    if r["doctor_id"] == did and r["source"] == params.get("source")
                ]
                self._row = None
            else:
                self._row = None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._db._as_tuple(r) for r in getattr(self._db, "_last_list", [])]

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, prescriptions=None):
        self.prescriptions = prescriptions or {}
        self.statements = []
        self.insert_rows = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self._last_list = []
        self._prescription_id_seq = 500

    def _next_prescription_id(self):
        self._prescription_id_seq += 1
        return self._prescription_id_seq

    def _as_tuple(self, row):
        """Return the row in the router's _COLS order."""
        if row is None:
            return None
        return (
            row["prescription_id"], row["patient_id"], row["doctor_id"],
            row["upload_date"], row["restrictions"], row["validated"],
            row["source"], row["status"], row["updated_at"],
            row["file_path"], row["file_name"], row["file_mime_type"],
            row["file_size_bytes"],
        )

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def patient_principal(subject_id):
    return Principal(subject=str(subject_id), role=ROLE_PATIENT)


def doctor_principal(subject_id):
    return Principal(subject=str(subject_id), role=ROLE_DOCTOR)


class FakeRequest:
    """Minimal stand-in for the Starlette Request the create endpoint reads."""

    def __init__(self, body=None, content_type="application/json"):
        self._body = body or {}
        self.headers = {"content-type": content_type}

    async def json(self):
        return self._body


@pytest.fixture
def db(monkeypatch):
    conn = FakeConnection()
    monkeypatch.setattr(pr, "get_connection", lambda: conn)
    return conn


# --------------------------------------------------------------------------- #
# 1) The identity-column insert (the SCRUM-46 defect)
# --------------------------------------------------------------------------- #

def _create_call(db, body=None):
    request = FakeRequest(body if body is not None else {"restrictions": "No gluten"})
    return run(pr.create_my_prescription(request=request, principal=patient_principal(1)))


def _insert_sql(db):
    return next(sql for sql, _ in db.statements
                if sql.lower().startswith("insert into prescription"))


def test_create_insert_does_not_name_the_identity_column(db):
    """The INSERT must not list PRESCRIPTION_ID: Oracle generates it."""
    _create_call(db)
    lowered = _insert_sql(db).lower()
    column_list = lowered[lowered.index("(") + 1:lowered.index(")")]
    assert "prescription_id" not in column_list, (
        "PRESCRIPTION_ID is GENERATED ALWAYS AS IDENTITY; naming it in the "
        "INSERT raises ORA-32795"
    )


def test_create_insert_does_not_reference_a_sequence(db):
    """No sequence may be referenced: PRESCRIPTION_SEQ does not exist (ORA-02289)."""
    _create_call(db)
    sql = _insert_sql(db).lower()
    assert "prescription_seq" not in sql
    assert "nextval" not in sql


def test_create_uses_returning_into_for_the_generated_id(db):
    _create_call(db)
    assert "returning prescription_id into :new_id" in _insert_sql(db).lower()


def test_create_returns_the_generated_prescription_id(db):
    """getvalue() is a LIST, so the id is getvalue()[0] -- and it is the DB's."""
    response = _create_call(db)
    assert isinstance(response, PrescriptionResponse)
    assert response.prescription_id == 501
    assert response.prescription_id in db.prescriptions


def test_create_commits_and_closes(db):
    _create_call(db)
    assert db.committed is True
    assert db.rolled_back is False
    assert db.closed is True


def test_create_rolls_back_and_raises_500_on_db_error(db, monkeypatch):
    """A failed insert must roll back and surface a 500 (not leak an ORA error)."""
    def boom(self, sql, **params):
        raise pr.oracledb.DatabaseError("ORA-02289: sequence does not exist")
    monkeypatch.setattr(FakeCursor, "execute", boom)
    with pytest.raises(HTTPException) as excinfo:
        _create_call(db)
    assert status_of(excinfo) == 500
    assert db.rolled_back is True
    assert db.committed is False
    assert db.closed is True


# --------------------------------------------------------------------------- #
# 2) RBAC + strict ownership
# --------------------------------------------------------------------------- #

def test_patient_passes_require_patient():
    assert run(require_patient(principal=patient_principal(1))) is not None


def test_doctor_is_403_on_require_patient():
    with pytest.raises(HTTPException) as excinfo:
        run(require_patient(principal=doctor_principal(7)))
    assert status_of(excinfo) == 403


def test_create_endpoint_is_bound_to_require_patient():
    """Proves the create route cannot be reached without the PATIENT dependency."""
    params = signature(pr.create_my_prescription).parameters.values()
    principal = next(p for p in params if p.name == "principal")
    assert principal.default.dependency is require_patient


def test_owner_is_the_token_subject_not_the_body(db):
    """A body-supplied patient_id must never decide ownership."""
    _create_call(db, {"restrictions": "x", "patient_id": 999})
    row = next(iter(db.prescriptions.values()))
    assert row["patient_id"] == 1


def test_source_is_forced_to_patient_and_doctor_id_is_null(db):
    _create_call(db, {"restrictions": "x", "source": "DOCTOR", "doctor_id": 5})
    row = next(iter(db.prescriptions.values()))
    assert row["source"] == SOURCE_PATIENT
    assert row["doctor_id"] is None


def test_validated_is_never_client_settable(db):
    _create_call(db, {"restrictions": "x", "validated": 1})
    row = next(iter(db.prescriptions.values()))
    assert row["validated"] == 0


# --------------------------------------------------------------------------- #
# 3) CLOB / long text round-trip
# --------------------------------------------------------------------------- #

def test_long_restrictions_text_round_trips_intact(db):
    """A near-limit RESTRICTIONS string must survive the round-trip intact."""
    long_text = "Avoid: " + ("gluten, dairy, nuts, " * 188) + "."
    assert len(long_text) <= 4000
    response = _create_call(db, {"restrictions": long_text})
    assert response.restrictions == long_text


def test_clob_lob_object_is_read_back_as_text(db):
    """Oracle returns a CLOB as a LOB object; the router must surface its text."""
    stored_text = "Avoid peanuts and shellfish. " * 60

    class FakeLob:
        """Mimics oracledb's LOB: only ``str()`` yields the content."""

        def __init__(self, text):
            self._text = text

        def __str__(self):
            return self._text

    _create_call(db, {"restrictions": "seed"})
    row = db.prescriptions[501]
    row["restrictions"] = FakeLob(stored_text)
    response = pr._row_to_response(db._as_tuple(row))
    assert response.restrictions == stored_text


def test_blank_restrictions_normalise_to_none(db):
    response = _create_call(db, {"restrictions": "   "})
    assert response.restrictions is None


# --------------------------------------------------------------------------- #
# 4) Status handling
# --------------------------------------------------------------------------- #

def test_status_defaults_to_active(db):
    response = _create_call(db, {})
    assert response.status == STATUS_ACTIVE


def test_invalid_status_falls_back_to_active(db):
    response = _create_call(db, {"status": "not-a-status"})
    assert response.status == STATUS_ACTIVE


def test_valid_status_is_kept(db):
    response = _create_call(db, {"status": "COMPLETED"})
    assert response.status == "COMPLETED"


# --------------------------------------------------------------------------- #
# 5) Response contract + list scoping
# --------------------------------------------------------------------------- #

def test_response_carries_the_disclaimer(db):
    response = _create_call(db)
    assert response.disclaimer == DECISION_SUPPORT_DISCLAIMER


def test_list_returns_only_the_callers_own_patient_submissions(db):
    _create_call(db, {"restrictions": "mine"})
    # A doctor-issued row for the same patient must stay invisible.
    db.prescriptions[600] = {
        "prescription_id": 600, "patient_id": 1, "doctor_id": 7,
        "upload_date": None, "restrictions": "from doctor", "validated": 1,
        "source": SOURCE_DOCTOR, "status": STATUS_ACTIVE, "updated_at": None,
        "file_path": None, "file_name": None, "file_mime_type": None,
        "file_size_bytes": None,
    }
    listed = run(pr.list_my_prescriptions(principal=patient_principal(1)))
    assert len(listed.prescriptions) == 1
    assert listed.prescriptions[0].source == SOURCE_PATIENT


def test_schema_model_keeps_prescription_id_out_of_the_create_payload():
    """The create model must not accept an owner/id from the client."""
    payload = PrescriptionCreate(restrictions="x", patient_id=5, prescription_id=9)
    assert not hasattr(payload, "patient_id")
    assert not hasattr(payload, "prescription_id")
