"""SCRUM-55: Dietary drift detection + adaptive compensator API (SRS 3.2.3.4).

    GET /api/drift/today?client_date=YYYY-MM-DD&client_time=HH:MM

Reads today's meals from the CURRENT plan (SCRUM-48/49), today's MEAL entries
from the Daily Log (SCRUM-53), the patient's conditions and allergies, and the
Dish DNA calories of logged dishes, then asks the pure rule engine in
``backend.services.drift_service`` what drifted and how to rebalance the rest
of the day.

Read-only: nothing is stored and no table is created or changed. Because the
result is recalculated from the log every time, it updates as soon as a meal
is logged, corrected or deleted. Identity comes only from the token.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import oracledb
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.core.security import Principal, require_patient
from backend.database.connection import get_connection
from backend.routers.meal_plan_router import today_index
from backend.services import daily_log_service, plan_service
from backend.services.drift_service import detect_and_compensate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/drift", tags=["Drift Compensator"])

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    read = getattr(value, "read", None)
    return str(read()) if callable(read) else str(value)


def _profile(cursor, patient_id: int):
    cursor.execute(
        "SELECT conditions, allergies FROM health_profile WHERE patient_id = :patient_id",
        patient_id=patient_id,
    )
    row = cursor.fetchone()
    return (_text(row[0]), _text(row[1])) if row else (None, None)


def _dishes(cursor, dish_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    facts: Dict[int, Dict[str, Any]] = {}
    for dish_id in dish_ids:
        cursor.execute(
            "SELECT calories, allergens FROM dish_dna WHERE dish_id = :dish_id",
            dish_id=dish_id,
        )
        row = cursor.fetchone()
        if row:
            facts[dish_id] = {"calories": row[0], "allergens": _text(row[1])}
    return facts


@router.get("/today")
def read_drift_today(
    client_date: Optional[date] = Query(None, description="The browser's local date (YYYY-MM-DD)."),
    client_time: Optional[str] = Query(None, description="The browser's local time (HH:MM)."),
    principal: Principal = Depends(require_patient),
):
    try:
        patient_id = int(principal.subject)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication token has an invalid subject.")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today = client_date if client_date and abs((client_date - now.date()).days) <= 1 else now.date()
    now_hhmm = client_time if client_time and _TIME_RE.match(client_time) else now.strftime("%H:%M")

    connection = get_connection()
    if connection is None:
        raise HTTPException(status_code=503, detail="Database connection failed")
    cursor = connection.cursor()
    try:
        row = plan_service.read_current_plan_row(cursor, patient_id)
        if row is None:
            return {"found": False, "message": "Create your meal plan first. Drift is measured against it."}
        plan = plan_service.row_to_plan(cursor, row)
        days = [d.model_dump() if hasattr(d, "model_dump") else d for d in plan.get("days", [])]
        if not days:
            return {"found": False, "message": "Your plan has no meals yet."}
        index = today_index(plan.get("generated_at"), today, len(days))
        meals = days[index].get("meals", [])

        entries = [daily_log_service.row_to_entry(r) for r in daily_log_service.read_day_rows(cursor, patient_id, today)]
        conditions, allergies = _profile(cursor, patient_id)
        dish_ids = sorted({e["dish_id"] for e in entries if e.get("log_type") == "MEAL" and e.get("dish_id")})
        dishes = _dishes(cursor, dish_ids)
    except oracledb.Error as error:
        logger.error("SCRUM-55 drift check failed: %s", error)
        raise HTTPException(status_code=500, detail="Unable to check today's drift right now.")
    finally:
        cursor.close()
        connection.close()

    result = detect_and_compensate(
        meals, entries, now_hhmm,
        calorie_target=plan.get("calorie_target"),
        conditions=conditions, allergies=allergies, dishes=dishes,
    )
    return {
        "found": True,
        "today_date": today.isoformat(),
        "now": now_hhmm,
        "plan_id": plan["plan_id"],
        "day_number": index + 1,
        **result,
    }
