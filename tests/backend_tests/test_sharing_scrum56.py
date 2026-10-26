"""SCRUM-56 tests: progress sharing + professional escalation (SRS 3.2.3.5).

Runs WITHOUT a live Oracle connection: both routers use a small in-memory
fake database.
"""

import inspect
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")

import oracledb
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.core.security import Principal, require_doctor, require_patient
from backend.routers import doctor_portal_router as doctor_api
from backend.routers import sharing_router as sharing_api
from backend.schemas.sharing import ReviewIn, ShareIn
from backend.services import sharing_service as share

PATIENT = Principal(subject="5", role="PATIENT")
OTHER_PATIENT = Principal(subject="6", role="PATIENT")
DOCTOR = Principal(subject="1", role="DOCTOR")
OTHER_DOCTOR = Principal(subject="2", role="DOCTOR")
TODAY = date.today()


# --------------------------------------------------------------------------- #
# Fake database
# --------------------------------------------------------------------------- #
class FakeDB:
    def __init__(self):
        self.doctors = {
            1: {"name": "Dr. Rahman", "spec": "Nutrition", "hash": "x"},
            2: {"name": "Dr. Akter", "spec": "Medicine", "hash": "y"},
            3: {"name": "Dr. Old Reference", "spec": None, "hash": None},  # cannot log in
        }
        self.patients = {5: "Fatema Test", 6: "Other Test"}
        self.shares = {}
        self.plans = {
            11: {"patient_id": 5, "status": "CURRENT", "requires_review": 1, "created_at": datetime(2026, 10, 20),
                 "conflict_notes": json.dumps([{"code": "c", "title": "Low salt vs high protein", "explanation": "x",
                                                "requires_review": True}])},
            10: {"patient_id": 5, "status": "SUPERSEDED", "requires_review": 1, "created_at": datetime(2026, 10, 1),
                 "conflict_notes": "[]"},
            21: {"patient_id": 6, "status": "CURRENT", "requires_review": 0, "created_at": datetime(2026, 10, 21),
                 "conflict_notes": "[]"},
        }
        self.reviews = []
        self.logs = [
            {"patient_id": 5, "log_date": TODAY, "log_type": "WATER", "status": None, "quantity": 500, "title": "Water"},
            {"patient_id": 5, "log_date": TODAY, "log_type": "MEAL", "status": "EATEN", "quantity": 1, "title": "Dal"},
            {"patient_id": 6, "log_date": TODAY, "log_type": "MEAL", "status": "EATEN", "quantity": 1, "title": "Rice"},
        ]
        self.missing_tables = False


class FakeVar:
    value = None

    def getvalue(self):
        return [self.value]


class FakeOracleError:
    code = 942


class FakeCursor:
    def __init__(self, db):
        self.db, self.res = db, []

    def var(self, _type):
        return FakeVar()

    def _share_row(self, key):
        s = self.db.shares[key]
        d = self.db.doctors[key[1]]
        return (s["id"], key[1], d["name"], d["spec"], s["daily_log"], s["plan"], s["progress"], s["status"], None)

    def _review_rows(self, field, value):
        rows = [r for r in self.db.reviews if r[field] == value]
        rows.sort(key=lambda r: (r["reviewed_at"], r["id"]), reverse=True)
        return [(r["id"], r["plan_id"], r["patient_id"], r["doctor_id"], self.db.doctors[r["doctor_id"]]["name"],
                 r["decision"], r["note"], r["reviewed_at"]) for r in rows]

    def execute(self, sql, **b):
        q = " ".join(sql.split())
        db = self.db
        self.res = []
        if db.missing_tables and ("share_permission" in q or "plan_review" in q):
            raise oracledb.DatabaseError(FakeOracleError())

        if q.startswith("SELECT doctor_id, name, specialization FROM doctor WHERE password_hash IS NOT NULL ORDER"):
            self.res = [(i, d["name"], d["spec"]) for i, d in sorted(db.doctors.items(), key=lambda x: x[1]["name"])
                        if d["hash"]]
        elif q.startswith("SELECT doctor_id, name, specialization FROM doctor WHERE doctor_id"):
            d = db.doctors.get(b["doctor_id"])
            self.res = [(b["doctor_id"], d["name"], d["spec"])] if d and d["hash"] else []
        elif q.startswith("SELECT s.share_id"):
            self.res = [self._share_row(k) for k in sorted(db.shares) if k[0] == b["patient_id"]]
        elif q.startswith("MERGE INTO share_permission"):
            key = (b["patient_id"], b["doctor_id"])
            existing = db.shares.get(key)
            db.shares[key] = {"id": existing["id"] if existing else len(db.shares) + 1, "daily_log": b["daily_log"],
                              "plan": b["plan"], "progress": b["progress"], "status": b["status"]}
        elif q.startswith("SELECT share_daily_log"):
            s = db.shares.get((b["patient_id"], b["doctor_id"]))
            self.res = [(s["daily_log"], s["plan"], s["progress"], s["status"])] if s else []
        elif q.startswith("SELECT s.patient_id, p.name"):
            self.res = [(k[0], db.patients[k[0]], s["daily_log"], s["plan"], s["progress"], None)
                        for k, s in sorted(db.shares.items()) if k[1] == b["doctor_id"] and s["status"] == "ACTIVE"]
        elif q.startswith("SELECT plan_id, requires_review, created_at, conflict_notes"):
            self.res = [(i, p["requires_review"], p["created_at"], p["conflict_notes"]) for i, p in db.plans.items()
                        if p["patient_id"] == b["patient_id"] and p["status"] == "CURRENT"]
        elif q.startswith("SELECT plan_id, patient_id, status, requires_review"):
            p = db.plans.get(b["plan_id"])
            self.res = [(b["plan_id"], p["patient_id"], p["status"], p["requires_review"])] if p else []
        elif q.startswith("SELECT r.review_id") and "WHERE r.plan_id" in q:
            self.res = self._review_rows("plan_id", b["plan_id"])
        elif q.startswith("SELECT r.review_id") and "WHERE r.patient_id" in q:
            self.res = self._review_rows("patient_id", b["patient_id"])
        elif q.startswith("INSERT INTO plan_review"):
            rid = len(db.reviews) + 1
            db.reviews.append({"id": rid, "plan_id": b["plan_id"], "patient_id": b["patient_id"],
                               "doctor_id": b["doctor_id"], "decision": b["decision"], "note": b["note"],
                               "reviewed_at": b["reviewed_at"]})
            b["new_id"].value = rid
        elif q.startswith("SELECT p.plan_id, p.patient_id, pt.name"):
            rows = []
            for i, p in db.plans.items():
                s = db.shares.get((p["patient_id"], b["doctor_id"]))
                if s and s["status"] == "ACTIVE" and s["plan"] and p["status"] == "CURRENT" and p["requires_review"]:
                    rows.append((i, p["patient_id"], db.patients[p["patient_id"]], p["created_at"], p["conflict_notes"]))
            self.res = rows
        elif q.startswith("SELECT log_id,"):
            self.res = [(n + 1, l["log_type"], l["log_date"], None, None, l["title"], None, l["quantity"], None,
                         l["status"], None, 11, None, None)
                        for n, l in enumerate(db.logs) if l["patient_id"] == b["patient_id"]
                        and b["start_date"] <= l["log_date"] <= b["end_date"]]
        elif q.startswith("SELECT log_date, log_type, status, quantity"):
            self.res = [(l["log_date"], l["log_type"], l["status"], l["quantity"]) for l in db.logs
                        if l["patient_id"] == b["patient_id"] and b["start_date"] <= l["log_date"] <= b["end_date"]]
        elif q.startswith("SELECT plan_id, patient_id, calorie_target"):
            self.res = [(i, p["patient_id"], 1800, "", p["created_at"], p["status"], "[]", "[]", "{}", "[]",
                         p["conflict_notes"], p["requires_review"]) for i, p in db.plans.items()
                        if p["patient_id"] == b["patient_id"] and p["status"] == b["status"]]
        elif q.startswith("SELECT activity_plan_id"):
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
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(sharing_api, "get_connection", lambda: FakeConnection(fake))
    monkeypatch.setattr(doctor_api, "get_connection", lambda: FakeConnection(fake))
    return fake


def share_with(doctor_id=1, daily_log=False, plan=False, progress=False, who=PATIENT):
    return sharing_api.save_my_sharing(
        ShareIn(doctor_id=doctor_id, daily_log=daily_log, plan=plan, progress=progress), principal=who)


def status_of(call):
    with pytest.raises(HTTPException) as error:
        call()
    return error.value.status_code


# --------------------------------------------------------------------------- #
# Who may call what (SCRUM-44 RBAC)
# --------------------------------------------------------------------------- #
def test_sharing_routes_are_patient_only():
    for fn in (sharing_api.read_doctors, sharing_api.read_my_sharing, sharing_api.save_my_sharing):
        assert inspect.signature(fn).parameters["principal"].default.dependency is require_patient


def test_doctor_view_routes_are_doctor_only():
    for fn in (doctor_api.read_my_patients, doctor_api.read_patient_daily_log, doctor_api.read_patient_progress,
               doctor_api.read_patient_plan, doctor_api.read_review_list, doctor_api.add_review):
        assert inspect.signature(fn).parameters["principal"].default.dependency is require_doctor


def test_no_route_takes_a_patient_id_from_the_body():
    assert "patient_id" not in ShareIn.model_fields
    assert "patient_id" not in ReviewIn.model_fields and "doctor_id" not in ReviewIn.model_fields


# --------------------------------------------------------------------------- #
# Input checks
# --------------------------------------------------------------------------- #
def test_share_needs_a_real_doctor_id():
    with pytest.raises(ValidationError):
        ShareIn(doctor_id=0, plan=True)


def test_changes_needed_review_must_say_what_to_change():
    with pytest.raises(ValidationError):
        ReviewIn(plan_id=11, decision="CHANGES_NEEDED", note="   ")
    assert ReviewIn(plan_id=11, decision="APPROVED").note is None


def test_unknown_review_decision_is_rejected():
    with pytest.raises(ValidationError):
        ReviewIn(plan_id=11, decision="MAYBE")


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #
def test_all_three_off_means_sharing_is_revoked():
    assert share.status_for({"daily_log": False, "plan": False, "progress": False}) == "REVOKED"
    assert share.status_for({"daily_log": False, "plan": True, "progress": False}) == "ACTIVE"


def test_a_revoked_permission_shows_nothing():
    revoked = {"daily_log": True, "plan": True, "progress": True, "status": "REVOKED"}
    assert not share.can_see(revoked, "plan") and not share.can_see(None, "plan")


def test_review_status_newest_review_wins():
    old = {"decision": "CHANGES_NEEDED"}
    new = {"decision": "APPROVED"}
    assert share.review_status(True, []) == "pending"
    assert share.review_status(False, []) == "not_needed"
    assert share.review_status(True, [new, old]) == "approved"


# --------------------------------------------------------------------------- #
# Patient side
# --------------------------------------------------------------------------- #
def test_doctor_list_skips_doctors_who_cannot_log_in(db):
    names = [d["name"] for d in sharing_api.read_doctors(principal=PATIENT)["doctors"]]
    assert names == ["Dr. Akter", "Dr. Rahman"]


def test_patient_shares_selected_items_with_one_doctor(db):
    result = share_with(1, daily_log=True, progress=True)
    assert result["status"] == "ACTIVE" and "Dr. Rahman" in result["message"]
    shared = result["shares"][0]
    assert shared["shares"] == {"daily_log": True, "plan": False, "progress": True}


def test_changing_a_share_updates_the_same_row(db):
    share_with(1, daily_log=True)
    share_with(1, plan=True)
    assert len(db.shares) == 1
    assert sharing_api.read_my_sharing(principal=PATIENT)["shares"][0]["shares"]["plan"] is True


def test_unknown_or_non_login_doctor_is_404(db):
    assert status_of(lambda: share_with(99, plan=True)) == 404
    assert status_of(lambda: share_with(3, plan=True)) == 404


def test_turning_everything_off_stops_sharing(db):
    share_with(1, daily_log=True, plan=True)
    result = share_with(1)
    assert result["status"] == "REVOKED" and result["active_count"] == 0
    assert doctor_api.read_my_patients(principal=DOCTOR)["patients"] == []
    assert status_of(lambda: doctor_api.read_patient_daily_log(5, days=7, principal=DOCTOR)) == 403


def test_patient_sees_review_status_of_current_plan(db):
    share_with(1, plan=True)
    review = sharing_api.read_my_sharing(principal=PATIENT)["plan_review"]
    assert review["plan_id"] == 11 and review["status"] == "pending"
    assert review["shared_with_a_doctor"] is True and review["conflicts"] == ["Low salt vs high protein"]


def test_missing_tables_give_a_setup_hint(db):
    db.missing_tables = True
    with pytest.raises(HTTPException) as error:
        sharing_api.read_my_sharing(principal=PATIENT)
    assert error.value.status_code == 503 and "apply_sharing_scrum56" in error.value.detail


# --------------------------------------------------------------------------- #
# Doctor view: only what was allowed (SRS 3.2.3.5.2)
# --------------------------------------------------------------------------- #
def test_doctor_sees_shared_daily_log(db):
    share_with(1, daily_log=True)
    result = doctor_api.read_patient_daily_log(5, days=7, principal=DOCTOR)
    assert [e["title"] for e in result["entries"]] == ["Water", "Dal"]
    assert result["summary"]["water_ml"] == 500


def test_doctor_cannot_see_items_that_were_not_shared(db):
    share_with(1, daily_log=True)
    assert status_of(lambda: doctor_api.read_patient_plan(5, principal=DOCTOR)) == 403
    assert status_of(lambda: doctor_api.read_patient_progress(5, days=7, principal=DOCTOR)) == 403


def test_another_doctor_sees_nothing(db):
    share_with(1, daily_log=True, plan=True, progress=True)
    assert status_of(lambda: doctor_api.read_patient_daily_log(5, days=7, principal=OTHER_DOCTOR)) == 403
    assert doctor_api.read_my_patients(principal=OTHER_DOCTOR)["patients"] == []


def test_doctor_cannot_read_a_patient_who_never_shared(db):
    share_with(1, daily_log=True)
    assert status_of(lambda: doctor_api.read_patient_daily_log(6, days=7, principal=DOCTOR)) == 403


def test_shared_progress_has_one_point_per_day(db):
    share_with(1, progress=True)
    result = doctor_api.read_patient_progress(5, days=7, principal=DOCTOR)
    assert len(result["points"]) == 7 and result["days_logged"] == 1


def test_patient_list_has_names_but_no_contact_details(db):
    share_with(1, plan=True)
    patient = doctor_api.read_my_patients(principal=DOCTOR)["patients"][0]
    assert patient["name"] == "Fatema Test" and patient["plan_review"] == "pending"
    assert "email" not in patient and "phone" not in patient


def test_doctor_sees_shared_current_plan(db):
    share_with(1, plan=True)
    result = doctor_api.read_patient_plan(5, principal=DOCTOR)
    assert result["found"] is True and result["plan"]["plan_id"] == 11 and result["review_status"] == "pending"


# --------------------------------------------------------------------------- #
# Escalation + review record (SRS 3.2.3.5.3, 3.2.3.5.4)
# --------------------------------------------------------------------------- #
def test_plan_needing_review_appears_in_the_doctors_list(db):
    share_with(1, plan=True)
    result = doctor_api.read_review_list(principal=DOCTOR)
    assert result["pending"] == 1 and result["reviews"][0]["plan_id"] == 11


def test_plan_not_shared_is_not_in_the_review_list(db):
    share_with(1, daily_log=True)
    assert doctor_api.read_review_list(principal=DOCTOR)["reviews"] == []


def test_review_records_who_and_when(db):
    share_with(1, plan=True)
    result = doctor_api.add_review(ReviewIn(plan_id=11, decision="APPROVED", note="Looks safe."), principal=DOCTOR)
    assert result["review_status"] == "approved"
    saved = db.reviews[0]
    assert saved["doctor_id"] == 1 and saved["patient_id"] == 5 and isinstance(saved["reviewed_at"], datetime)
    assert doctor_api.read_review_list(principal=DOCTOR)["pending"] == 0
    history = sharing_api.read_my_sharing(principal=PATIENT)["history"]
    assert history[0]["doctor"]["name"] == "Dr. Rahman" and history[0]["note"] == "Looks safe."


def test_review_of_a_plan_that_was_not_shared_is_403(db):
    assert status_of(lambda: doctor_api.add_review(ReviewIn(plan_id=21, decision="APPROVED"), principal=DOCTOR)) == 403
    assert db.reviews == []


def test_review_of_unknown_plan_is_404(db):
    assert status_of(lambda: doctor_api.add_review(ReviewIn(plan_id=999, decision="APPROVED"), principal=DOCTOR)) == 404


def test_review_of_an_old_plan_is_409(db):
    share_with(1, plan=True)
    assert status_of(lambda: doctor_api.add_review(ReviewIn(plan_id=10, decision="APPROVED"), principal=DOCTOR)) == 409


def test_newer_review_replaces_the_status_but_keeps_history(db):
    share_with(1, plan=True)
    doctor_api.add_review(ReviewIn(plan_id=11, decision="CHANGES_NEEDED", note="Lower the salt."), principal=DOCTOR)
    db.reviews[0]["reviewed_at"] -= timedelta(minutes=5)
    doctor_api.add_review(ReviewIn(plan_id=11, decision="APPROVED"), principal=DOCTOR)
    review = sharing_api.read_my_sharing(principal=PATIENT)["plan_review"]
    assert review["status"] == "approved" and len(review["reviews"]) == 2
