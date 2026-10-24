"""SCRUM-51: Smart Notification & Alerting endpoints (SRS 3.1.3.4).

Endpoints
---------
    GET    /api/notifications/reminders               my reminder schedules
    POST   /api/notifications/reminders               add a reminder
    POST   /api/notifications/reminders/starter       add a starter set (only if I have none)
    PUT    /api/notifications/reminders/{id}          edit / enable / disable
    DELETE /api/notifications/reminders/{id}          remove
    GET    /api/notifications/feed                    today's due reminders + safety alerts
    POST   /api/notifications/events/{event_id}/ack   mark an alert done / dismissed
    GET    /api/notifications/stats?days=7            delivery & acknowledgement summary

Identity comes ONLY from the verified access token (SCRUM-44 ``require_patient``);
no route accepts a patient id, and another patient's record answers 404.

The feed endpoint is what "delivers" a notification: the first time a due
reminder or safety alert is returned it is recorded in NOTIFICATION_EVENT,
and acknowledging it records when and how the user responded
(SRS 3.1.3.4.5). The browser may pass its own date/time so reminders fire in
the user's local time.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Optional

import oracledb
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.core.security import Principal, require_patient
from backend.database.connection import get_connection
from backend.schemas.notification import (
    MAX_REMINDERS,
    AckIn,
    DeleteResponse,
    FeedItem,
    FeedResponse,
    ReminderIn,
    ReminderListResponse,
    ReminderOut,
    StatsResponse,
)
from backend.services import notification_service as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


# --------------------------------------------------------------------------- #
# Shared helpers
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
    logger.error("SCRUM-51 notification %s failed: %s", action, error)
    code = getattr(error.args[0] if error.args else None, "code", None)
    if code in (942, 904):  # table / column missing
        return HTTPException(
            status_code=503,
            detail=(
                "Notifications are not set up in this database yet. "
                "Run: python backend/database/apply_notification_scrum51.py"
            ),
        )
    return HTTPException(status_code=500, detail=f"Unable to {action} right now.")


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="That reminder was not found.")


def _now() -> datetime:
    """Server local time (a function so tests can freeze it)."""
    return datetime.now()


def _client_moment(client_date: Optional[date], client_time: Optional[str]):
    now = _now()
    today = client_date or now.date()
    if abs((today - now.date()).days) > 1:  # ignore nonsense client clocks
        today = now.date()
    hhmm = client_time if client_time and _TIME_RE.match(client_time) else now.strftime("%H:%M")
    return today, hhmm


# --------------------------------------------------------------------------- #
# Reminders
# --------------------------------------------------------------------------- #
@router.get("/reminders", response_model=ReminderListResponse)
def list_my_reminders(principal: Principal = Depends(require_patient)):
    patient_id = _patient_pk(principal)
    connection = _require_connection()
    cursor = connection.cursor()
    try:
        reminders = svc.list_reminders(cursor, patient_id)
        return ReminderListResponse(
            reminders=[ReminderOut.model_validate(r) for r in reminders],
            total=len(reminders),
        )
    except oracledb.Error as error:
        raise _oracle_failure(error, "read your reminders")
    finally:
        cursor.close()
        connection.close()


@router.post("/reminders", response_model=ReminderOut, status_code=201)
def add_my_reminder(payload: ReminderIn, principal: Principal = Depends(require_patient)):
    patient_id = _patient_pk(principal)
    connection = _require_connection()
    cursor = connection.cursor()
    try:
        if svc.count_reminders(cursor, patient_id) >= MAX_REMINDERS:
            raise HTTPException(
                status_code=422,
                detail=f"You can have up to {MAX_REMINDERS} reminders. Delete one you no longer need.",
            )
        new_id = svc.insert_reminder(cursor, patient_id, payload)
        connection.commit()
        return ReminderOut.model_validate(svc.read_reminder(cursor, patient_id, new_id))
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        raise _oracle_failure(error, "save your reminder")
    finally:
        cursor.close()
        connection.close()


@router.post("/reminders/starter", response_model=ReminderListResponse, status_code=201)
def add_starter_reminders(principal: Principal = Depends(require_patient)):
    patient_id = _patient_pk(principal)
    connection = _require_connection()
    cursor = connection.cursor()
    try:
        if svc.count_reminders(cursor, patient_id) > 0:
            raise HTTPException(status_code=409, detail="You already have reminders set up.")
        for item in svc.STARTER_REMINDERS:
            svc.insert_reminder(cursor, patient_id, ReminderIn(**item))
        connection.commit()
        reminders = svc.list_reminders(cursor, patient_id)
        return ReminderListResponse(
            reminders=[ReminderOut.model_validate(r) for r in reminders],
            total=len(reminders),
        )
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        raise _oracle_failure(error, "add the starter reminders")
    finally:
        cursor.close()
        connection.close()


@router.put("/reminders/{notification_id}", response_model=ReminderOut)
def edit_my_reminder(
    notification_id: int,
    payload: ReminderIn,
    principal: Principal = Depends(require_patient),
):
    patient_id = _patient_pk(principal)
    connection = _require_connection()
    cursor = connection.cursor()
    try:
        if not svc.update_reminder(cursor, patient_id, notification_id, payload):
            raise _not_found()
        connection.commit()
        return ReminderOut.model_validate(svc.read_reminder(cursor, patient_id, notification_id))
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        raise _oracle_failure(error, "update your reminder")
    finally:
        cursor.close()
        connection.close()


@router.delete("/reminders/{notification_id}", response_model=DeleteResponse)
def delete_my_reminder(notification_id: int, principal: Principal = Depends(require_patient)):
    patient_id = _patient_pk(principal)
    connection = _require_connection()
    cursor = connection.cursor()
    try:
        if not svc.delete_reminder(cursor, patient_id, notification_id):
            raise _not_found()
        connection.commit()
        return DeleteResponse(notification_id=notification_id)
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        raise _oracle_failure(error, "delete your reminder")
    finally:
        cursor.close()
        connection.close()
        # --------------------------------------------------------------------------- #
# Feed (delivery) + acknowledgement
# --------------------------------------------------------------------------- #
@router.get("/feed", response_model=FeedResponse)
def read_my_feed(
    client_date: Optional[date] = Query(None),
    client_time: Optional[str] = Query(None),
    principal: Principal = Depends(require_patient),
):
    patient_id = _patient_pk(principal)
    today, hhmm = _client_moment(client_date, client_time)

    connection = _require_connection()
    cursor = connection.cursor()
    try:
        reminders = svc.list_reminders(cursor, patient_id)
        events = svc.due_reminders(reminders, today, hhmm)
        events += svc.safety_alerts(
            today,
            svc.read_missed_meds(cursor, patient_id, today),
            svc.read_today_meals(cursor, patient_id, today),
            svc.read_allergies(cursor, patient_id),
            svc.read_plan_requires_review(cursor, patient_id),
        )
        for event in events:
            svc.record_event(cursor, patient_id, today, event)
        connection.commit()

        items = svc.sort_feed(svc.read_events_for_day(cursor, patient_id, today))
        return FeedResponse(
            feed_date=today,
            feed_time=hhmm,
            items=[FeedItem.model_validate(i) for i in items],
            unread=sum(1 for i in items if i["acknowledged_at"] is None),
        )
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        raise _oracle_failure(error, "load your notifications")
    finally:
        cursor.close()
        connection.close()


@router.post("/events/{event_id}/ack", response_model=FeedItem)
def acknowledge_my_event(
    event_id: int,
    payload: AckIn,
    principal: Principal = Depends(require_patient),
):
    patient_id = _patient_pk(principal)
    connection = _require_connection()
    cursor = connection.cursor()
    try:
        if not svc.acknowledge_event(cursor, patient_id, event_id, payload.action):
            raise HTTPException(status_code=404, detail="That notification was not found.")
        connection.commit()
        cursor.execute(
            f"SELECT {svc.EVENT_COLS} FROM notification_event "
            "WHERE event_id = :event_id AND patient_id = :patient_id",
            event_id=event_id,
            patient_id=patient_id,
        )
        return FeedItem.model_validate(svc.row_to_event(cursor.fetchone()))
    except HTTPException:
        connection.rollback()
        raise
    except oracledb.Error as error:
        connection.rollback()
        raise _oracle_failure(error, "update the notification")
    finally:
        cursor.close()
        connection.close()


@router.get("/stats", response_model=StatsResponse)
def read_my_stats(
    days: int = Query(7, ge=1, le=90),
    principal: Principal = Depends(require_patient),
):
    patient_id = _patient_pk(principal)
    connection = _require_connection()
    cursor = connection.cursor()
    try:
        start = svc.stats_start(_now().date(), days)
        rows = svc.read_event_rows_since(cursor, patient_id, start)
        return StatsResponse(**svc.build_stats(rows, days))
    except oracledb.Error as error:
        raise _oracle_failure(error, "read your reminder history")
    finally:
        cursor.close()
        connection.close()