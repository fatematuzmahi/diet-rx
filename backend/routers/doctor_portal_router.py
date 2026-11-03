"""SCRUM-56: doctor view + professional review (SRS 3.2.3.5.2 - 3.2.3.5.4).

    GET  /api/doctor/patients                              patients who share with me
    GET  /api/doctor/patients/{patient_id}/daily-log?days= their Daily Log   (if shared)
    GET  /api/doctor/patients/{patient_id}/progress?days=  their progress    (if shared)
    GET  /api/doctor/patients/{patient_id}/plan            their current plan (if shared)
    GET  /api/doctor/reviews                               plans waiting for review
    POST /api/doctor/reviews                               record a review

Every route is DOCTOR-only (require_doctor). The doctor id comes only from the
token. A doctor sees ONLY what the patient switched on for that doctor;
anything else answers 403 (SRS 3.2.3.5.2). Reviews are insert-only, so the
record of who reviewed what and when is kept (SRS 3.2.3.5.4).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, Optional

import oracledb
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.core.security import Principal, require_doctor
from backend.database.connection import get_connection
from backend.schemas import daily_log as log_schemas
from backend.schemas.sharing import ReviewIn
from backend.services import daily_log_service as log_svc
from backend.services import plan_service
from backend.services import sharing_service as share

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/doctor", tags=["Doctor View"])

SETUP_HINT = "Progress sharing is not set up yet. Run: python backend/database/apply_sharing_scrum56.py"


def _code(error: oracledb.Error) -> Optional[int]:
    return getattr(error.args[0] if error.args else None, "code", None)


def _doctor_pk(principal: Principal) -> int:
    try:
        return int(principal.subject)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication token has an invalid subject.")


def _connect():
    connection = get_connection()
    if connection is None:
        raise HTTPException(status_code=503, detail="Database connection failed")
    return connection


def _fail(error: oracledb.Error, action: str) -> HTTPException:
    logger.error("SCRUM-56 doctor %s failed: %s", action, error)
    if _code(error) in (942, 904):
        return HTTPException(status_code=503, detail=SETUP_HINT)
    return HTTPException(status_code=500, detail=f"Unable to {action} right now.")


def _require_share(cursor, patient_id: int, doctor_id: int, item: str) -> None:
    """403 unless the patient switched this item on for this doctor."""
    permission = share.read_permission(cursor, patient_id, doctor_id)
    if not share.can_see(permission, item):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This patient has not shared their {share.ITEM_LABELS[item]} with you.",
        )


def _range(days: int):
    end = log_schemas._today()
    return end - timedelta(days=days - 1), end


# --------------------------------------------------------------------------- #
# Patients who share with me
# --------------------------------------------------------------------------- #
@router.get("/patients")
def read_my_patients(principal: Principal = Depends(require_doctor)) -> Dict[str, Any]:
    doctor_id = _doctor_pk(principal)
    connection = _connect()
    cursor = connection.cursor()
    try:
        patients = share.read_doctor_patients(cursor, doctor_id)
        for patient in patients:
            plan = share.read_current_plan_brief(cursor, patient["patient_id"]) if patient["shares"]["plan"] else None
            if plan:
                reviews = share.read_plan_reviews(cursor, plan["plan_id"])
                patient["plan_review"] = share.review_status(plan["requires_review"], reviews)
            else:
                patient["plan_review"] = None
        return {"patients": patients}
    except oracledb.Error as error:
        raise _fail(error, "load your patients")
    finally:
        cursor.close()
        connection.close()


@router.get("/patients/{patient_id}/daily-log")
def read_patient_daily_log(
    patient_id: int,
    days: int = Query(7, ge=1, le=30),
    principal: Principal = Depends(require_doctor),
) -> Dict[str, Any]:
    doctor_id = _doctor_pk(principal)
    connection = _connect()
    cursor = connection.cursor()
    try:
        _require_share(cursor, patient_id, doctor_id, "daily_log")
        start, end = _range(days)
        entries = [log_svc.row_to_entry(r) for r in share.read_log_entries(cursor, patient_id, start, end)]
        return {"patient_id": patient_id, "days": days, "start_date": start, "end_date": end,
                "entries": entries, "summary": log_svc.summarize(entries)}
    except HTTPException:
        raise
    except oracledb.Error as error:
        raise _fail(error, "load this Daily Log")
    finally:
        cursor.close()
        connection.close()


@router.get("/patients/{patient_id}/progress")
def read_patient_progress(
    patient_id: int,
    days: int = Query(7, ge=1, le=90),
    principal: Principal = Depends(require_doctor),
) -> Dict[str, Any]:
    doctor_id = _doctor_pk(principal)
    connection = _connect()
    cursor = connection.cursor()
    try:
        _require_share(cursor, patient_id, doctor_id, "progress")
        start, end = _range(days)
        rows = log_svc.read_range_rows(cursor, patient_id, start, end)
        return {"patient_id": patient_id, "days": days, "start_date": start, "end_date": end,
                **log_svc.build_trend(rows, start, end)}
    except HTTPException:
        raise
    except oracledb.Error as error:
        raise _fail(error, "load this progress")
    finally:
        cursor.close()
        connection.close()


@router.get("/patients/{patient_id}/plan")
def read_patient_plan(patient_id: int, principal: Principal = Depends(require_doctor)) -> Dict[str, Any]:
    doctor_id = _doctor_pk(principal)
    connection = _connect()
    cursor = connection.cursor()
    try:
        _require_share(cursor, patient_id, doctor_id, "plan")
        row = plan_service.read_current_plan_row(cursor, patient_id)
        if row is None:
            return {"found": False, "message": "This patient has no current plan yet."}
        plan = plan_service.row_to_plan(cursor, row)
        reviews = share.read_plan_reviews(cursor, plan["plan_id"])
        return {"found": True, "plan": plan, "reviews": reviews,
                "review_status": share.review_status(plan["requires_review"], reviews)}
    except HTTPException:
        raise
    except oracledb.Error as error:
        raise _fail(error, "load this plan")
    finally:
        cursor.close()
        connection.close()


# --------------------------------------------------------------------------- #
# Professional review (escalation)
# --------------------------------------------------------------------------- #
@router.get("/reviews")
def read_review_list(principal: Principal = Depends(require_doctor)) -> Dict[str, Any]:
    doctor_id = _doctor_pk(principal)
    connection = _connect()
    cursor = connection.cursor()
    try:
        queue = share.read_review_queue(cursor, doctor_id)
        return {"reviews": queue, "pending": sum(1 for q in queue if q["review_status"] == "pending")}
    except oracledb.Error as error:
        raise _fail(error, "load the review list")
    finally:
        cursor.close()
        connection.close()


@router.post("/reviews", status_code=201)
def add_review(payload: ReviewIn, principal: Principal = Depends(require_doctor)) -> Dict[str, Any]:
    doctor_id = _doctor_pk(principal)
    connection = _connect()
    cursor = connection.cursor()
    try:
        owner = share.read_plan_owner(cursor, payload.plan_id)
        if owner is None:
            raise HTTPException(status_code=404, detail="That plan was not found.")
        _require_share(cursor, owner["patient_id"], doctor_id, "plan")
        if owner["status"] != "CURRENT":
            raise HTTPException(status_code=409,
                                detail="This plan was replaced by a newer one. Please review the current plan.")
        review_id = share.insert_review(cursor, payload.plan_id, owner["patient_id"], doctor_id,
                                        payload.decision, payload.note)
        connection.commit()
        reviews = share.read_plan_reviews(cursor, payload.plan_id)
        saved = next((r for r in reviews if r["review_id"] == review_id), None)
        return {"message": "Review saved.", "review": saved,
                "review_status": share.review_status(owner["requires_review"], reviews)}
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        raise _fail(error, "save your review")
    finally:
        cursor.close()
        connection.close()
