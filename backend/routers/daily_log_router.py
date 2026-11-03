"""SCRUM-53: Daily Logging endpoints (SRS 3.2.3.2).

Endpoints
---------
    GET    /api/daily-log?log_date=YYYY-MM-DD   one day's entries + summary
    POST   /api/daily-log                       add an entry
    PUT    /api/daily-log/{log_id}              correct an entry
    DELETE /api/daily-log/{log_id}              remove an entry
    GET    /api/daily-log/trend?days=7|30       weekly / monthly progress

Identity & ownership
--------------------
The patient comes ONLY from the verified access token (``require_patient``,
SCRUM-44). No route accepts a ``patient_id``; every query filters on the
token's subject, so another patient's entry answers 404, never 403, and its
existence is not revealed.

Plan association (SRS 3.2.3.2.2)
--------------------------------
A new entry is linked to the patient's CURRENT plan. A correction keeps the
plan the entry was first recorded under. Logging is still allowed when no
plan exists yet; ``plan_id`` is then null and the UI says so.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

import oracledb
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.core.security import Principal, require_patient
from backend.database.connection import get_connection
from backend.schemas.daily_log import (
    MAX_BACKFILL_DAYS,
    MAX_WATER_ML_PER_DAY,
    DailyLogDayResponse,
    DailyLogDeleteResponse,
    DailyLogEntryIn,
    DailyLogEntryOut,
    DailyLogTrendResponse,
)
from backend.schemas import daily_log as log_schemas
from backend.services import daily_log_service as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/daily-log", tags=["Daily Log"])


# --------------------------------------------------------------------------- #
# Shared helpers (same conventions as the SCRUM-45/48 routers)
# --------------------------------------------------------------------------- #
def _require_connection():
    connection = get_connection()
    if connection is None:
        raise HTTPException(status_code=503, detail="Database connection failed")
    return connection


def _patient_pk(principal: Principal) -> int:
    try:
        return int(principal.subject)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has an invalid subject.",
        )


def _oracle_failure(error: oracledb.Error, action: str) -> HTTPException:
    """Log the full Oracle error and turn it into a friendly HTTP error."""
    logger.error("SCRUM-53 daily log %s failed: %s", action, error)
    code = getattr(error.args[0] if error.args else None, "code", None)
    if code == 942:  # ORA-00942: table or view does not exist
        return HTTPException(
            status_code=503,
            detail=(
                "Daily logging is not set up in this database yet. "
                "Run: python backend/database/apply_daily_log_scrum53.py"
            ),
        )
    return HTTPException(status_code=500, detail=f"Unable to {action} right now.")


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="That log entry was not found.")


def _resolve_title(cursor, entry: DailyLogEntryIn) -> Optional[str]:
    """Check a picked Dish DNA dish exists and use its name if no title was typed."""
    if entry.log_type != "MEAL" or entry.dish_id is None:
        return entry.title
    dish_name = svc.read_dish_name(cursor, entry.dish_id)
    if dish_name is None:
        raise HTTPException(
            status_code=422,
            detail="That dish is no longer in Dish DNA. Type the food name instead.",
        )
    return entry.title or dish_name


def _check_daily_water(cursor, patient_id: int, entry: DailyLogEntryIn, exclude_id=None):
    if entry.log_type != "WATER":
        return
    already = svc.water_total_for_day(cursor, patient_id, entry.log_date, exclude_id)
    if already + float(entry.quantity or 0) > MAX_WATER_ML_PER_DAY:
        raise HTTPException(
            status_code=422,
            detail=(
                f"That would make more than {MAX_WATER_ML_PER_DAY // 1000} litres of "
                "water for this day. Please check the amount."
            ),
        )


# --------------------------------------------------------------------------- #
# GET /api/daily-log/trend -- weekly / monthly progress (SRS 3.2.3.2.4)
# --------------------------------------------------------------------------- #
@router.get("/trend", response_model=DailyLogTrendResponse)
def read_my_trend(
    days: int = Query(7, ge=1, le=90),
    principal: Principal = Depends(require_patient),
):
    patient_id = _patient_pk(principal)
    end = log_schemas._today()
    start = end - timedelta(days=days - 1)

    connection = _require_connection()
    cursor = connection.cursor()
    try:
        rows = svc.read_range_rows(cursor, patient_id, start, end)
        trend = svc.build_trend(rows, start, end)
        return DailyLogTrendResponse(days=days, start_date=start, end_date=end, **trend)
    except oracledb.Error as error:
        raise _oracle_failure(error, "read your progress")
    finally:
        cursor.close()
        connection.close()


# --------------------------------------------------------------------------- #
# GET /api/daily-log -- one day
# --------------------------------------------------------------------------- #
@router.get("", response_model=DailyLogDayResponse)
def read_my_day(
    log_date: Optional[date] = Query(None),
    principal: Principal = Depends(require_patient),
):
    patient_id = _patient_pk(principal)
    day = log_date or log_schemas._today()

    connection = _require_connection()
    cursor = connection.cursor()
    try:
        entries = [svc.row_to_entry(row) for row in svc.read_day_rows(cursor, patient_id, day)]
        plan_id = svc.find_current_plan_id(cursor, patient_id)
        today = log_schemas._today()
        editable = today - timedelta(days=MAX_BACKFILL_DAYS) <= day <= today
        return DailyLogDayResponse(
            log_date=day,
            plan_id=plan_id,
            editable=editable,
            entries=[DailyLogEntryOut.model_validate(entry) for entry in entries],
            summary=svc.summarize(entries),
        )
    except oracledb.Error as error:
        raise _oracle_failure(error, "read your log")
    finally:
        cursor.close()
        connection.close()


# --------------------------------------------------------------------------- #
# POST /api/daily-log -- add an entry
# --------------------------------------------------------------------------- #
@router.post("", response_model=DailyLogEntryOut, status_code=201)
def add_my_entry(
    payload: DailyLogEntryIn,
    principal: Principal = Depends(require_patient),
):
    patient_id = _patient_pk(principal)

    connection = _require_connection()
    cursor = connection.cursor()
    try:
        title = _resolve_title(cursor, payload)
        _check_daily_water(cursor, patient_id, payload)
        plan_id = svc.find_current_plan_id(cursor, patient_id)
        log_id = svc.insert_entry(cursor, patient_id, plan_id, payload, title)
        connection.commit()
        row = svc.read_entry_row(cursor, patient_id, log_id)
        if row is None:  # pragma: no cover - defensive
            raise HTTPException(status_code=500, detail="The entry could not be read back.")
        return DailyLogEntryOut.model_validate(svc.row_to_entry(row))
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        raise _oracle_failure(error, "save your entry")
    finally:
        cursor.close()
        connection.close()


# --------------------------------------------------------------------------- #
# PUT /api/daily-log/{log_id} -- correct an entry (SRS 3.2.3.2.3)
# --------------------------------------------------------------------------- #
@router.put("/{log_id}", response_model=DailyLogEntryOut)
def correct_my_entry(
    log_id: int,
    payload: DailyLogEntryIn,
    principal: Principal = Depends(require_patient),
):
    patient_id = _patient_pk(principal)

    connection = _require_connection()
    cursor = connection.cursor()
    try:
        existing = svc.read_entry_row(cursor, patient_id, log_id)
        if existing is None:
            raise _not_found()
        current = svc.row_to_entry(existing)
        if current["log_date"] < log_schemas._today() - timedelta(days=MAX_BACKFILL_DAYS):
            raise HTTPException(
                status_code=422,
                detail=f"Entries older than {MAX_BACKFILL_DAYS} days can't be changed.",
            )

        title = _resolve_title(cursor, payload)
        _check_daily_water(cursor, patient_id, payload, exclude_id=log_id)
        plan_id = current["plan_id"] or svc.find_current_plan_id(cursor, patient_id)

        svc.update_entry(cursor, patient_id, log_id, payload, title, plan_id)
        connection.commit()
        row = svc.read_entry_row(cursor, patient_id, log_id)
        return DailyLogEntryOut.model_validate(svc.row_to_entry(row))
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        raise _oracle_failure(error, "update your entry")
    finally:
        cursor.close()
        connection.close()


# --------------------------------------------------------------------------- #
# DELETE /api/daily-log/{log_id}
# --------------------------------------------------------------------------- #
@router.delete("/{log_id}", response_model=DailyLogDeleteResponse)
def delete_my_entry(
    log_id: int,
    principal: Principal = Depends(require_patient),
):
    patient_id = _patient_pk(principal)

    connection = _require_connection()
    cursor = connection.cursor()
    try:
        existing = svc.read_entry_row(cursor, patient_id, log_id)
        if existing is None:
            raise _not_found()
        if svc.row_to_entry(existing)["log_date"] < log_schemas._today() - timedelta(days=MAX_BACKFILL_DAYS):
            raise HTTPException(
                status_code=422,
                detail=f"Entries older than {MAX_BACKFILL_DAYS} days can't be changed.",
            )
        svc.delete_entry(cursor, patient_id, log_id)
        connection.commit()
        return DailyLogDeleteResponse(log_id=log_id)
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        raise _oracle_failure(error, "delete your entry")
    finally:
        cursor.close()
        connection.close()