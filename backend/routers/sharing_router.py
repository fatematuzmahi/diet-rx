"""SCRUM-56: progress sharing -- the patient side (SRS 3.2.3.5).

    GET /api/sharing/doctors   doctors the patient can share with
    GET /api/sharing/me        what I share with whom + review status of my plan
    PUT /api/sharing/me        share with one doctor / change / turn off

The patient decides what each doctor may see: Daily Log, diet plan and
progress, each switched on or off on its own (SRS 3.2.3.5.1). Turning all three
off stops sharing; the row is kept with STATUS = 'REVOKED' (never deleted).

Identity comes only from the access token (require_patient).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import oracledb
from fastapi import APIRouter, Depends, HTTPException, status

from backend.core.security import Principal, require_patient
from backend.database.connection import get_connection
from backend.schemas.sharing import ShareIn
from backend.services import sharing_service as share

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sharing", tags=["Progress Sharing"])

SETUP_HINT = "Progress sharing is not set up yet. Run: python backend/database/apply_sharing_scrum56.py"


def _code(error: oracledb.Error) -> Optional[int]:
    return getattr(error.args[0] if error.args else None, "code", None)


def _patient_pk(principal: Principal) -> int:
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
    logger.error("SCRUM-56 %s failed: %s", action, error)
    if _code(error) in (942, 904):
        return HTTPException(status_code=503, detail=SETUP_HINT)
    return HTTPException(status_code=500, detail=f"Unable to {action} right now.")


def _overview(cursor, patient_id: int) -> Dict[str, Any]:
    shares = share.read_patient_shares(cursor, patient_id)
    plan = share.read_current_plan_brief(cursor, patient_id)
    plan_reviews = share.read_plan_reviews(cursor, plan["plan_id"]) if plan else []
    sharing_plan = any(s["status"] == share.STATUS_ACTIVE and s["shares"]["plan"] for s in shares)
    review = None
    if plan:
        review = {
            "plan_id": plan["plan_id"],
            "requires_review": plan["requires_review"],
            "conflicts": plan["conflicts"],
            "status": share.review_status(plan["requires_review"], plan_reviews),
            "shared_with_a_doctor": sharing_plan,
            "reviews": plan_reviews,
        }
    return {
        "shares": shares,
        "active_count": sum(1 for s in shares if s["status"] == share.STATUS_ACTIVE),
        "plan_review": review,
        "history": share.read_patient_reviews(cursor, patient_id),
    }


@router.get("/doctors")
def read_doctors(principal: Principal = Depends(require_patient)) -> Dict[str, Any]:
    _patient_pk(principal)
    connection = _connect()
    cursor = connection.cursor()
    try:
        return {"doctors": share.list_doctors(cursor)}
    except oracledb.Error as error:
        raise _fail(error, "load the doctor list")
    finally:
        cursor.close()
        connection.close()


@router.get("/me")
def read_my_sharing(principal: Principal = Depends(require_patient)) -> Dict[str, Any]:
    patient_id = _patient_pk(principal)
    connection = _connect()
    cursor = connection.cursor()
    try:
        return _overview(cursor, patient_id)
    except oracledb.Error as error:
        raise _fail(error, "load your sharing settings")
    finally:
        cursor.close()
        connection.close()


@router.put("/me")
def save_my_sharing(payload: ShareIn, principal: Principal = Depends(require_patient)) -> Dict[str, Any]:
    patient_id = _patient_pk(principal)
    connection = _connect()
    cursor = connection.cursor()
    try:
        doctor = share.read_doctor(cursor, payload.doctor_id)
        if doctor is None:
            raise HTTPException(status_code=404, detail="That doctor was not found.")
        flags = share.flags_from(payload)
        new_status = share.save_share(cursor, patient_id, payload.doctor_id, flags)
        connection.commit()
        if new_status == share.STATUS_REVOKED:
            message = f"Sharing with {doctor['name']} is turned off."
        else:
            parts = [share.ITEM_LABELS[item] for item, on in flags.items() if on]
            message = f"{doctor['name']} can now see your " + ", ".join(parts) + "."
        return {"message": message, "status": new_status, **_overview(cursor, patient_id)}
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        raise _fail(error, "save your sharing settings")
    finally:
        cursor.close()
        connection.close()
