"""SCRUM-53: Daily Logging persistence and aggregation (SRS 3.2.3.2).

Every function takes an explicit ``patient_id`` that the router derived from
the verified access token, and every query filters on it, so a patient can
only ever read, correct or delete their OWN entries.

Bind-variable names deliberately avoid Oracle reserved words (``:current``
caused ORA-01745 in SCRUM-48).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

import oracledb

from backend.schemas.daily_log import DailyLogEntryIn

#: Column order used by ``row_to_entry``.
LOG_COLS = (
    "log_id, log_type, log_date, log_time, slot, title, dish_id, quantity, "
    "unit, status, notes, plan_id, created_at, updated_at"
)


# --------------------------------------------------------------------------- #
# Pure helpers (no database)
# --------------------------------------------------------------------------- #
def _as_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value


def _as_number(value):
    if value is None:
        return None
    number = float(value)
    return int(number) if number.is_integer() else number


def row_to_entry(row) -> Dict[str, Any]:
    (
        log_id, log_type, log_date, log_time, slot, title, dish_id, quantity,
        unit, status, notes, plan_id, created_at, updated_at,
    ) = row
    return {
        "log_id": int(log_id),
        "log_type": log_type,
        "log_date": _as_date(log_date),
        "log_time": log_time,
        "slot": slot,
        "title": title,
        "dish_id": int(dish_id) if dish_id is not None else None,
        "quantity": _as_number(quantity),
        "unit": unit,
        "status": status,
        "notes": notes,
        "plan_id": int(plan_id) if plan_id is not None else None,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def empty_summary() -> Dict[str, Any]:
    return {
        "water_ml": 0,
        "meals": 0,
        "activity_min": 0,
        "activity_sessions": 0,
        "meds_taken": 0,
        "meds_total": 0,
        "goals_achieved": 0,
        "goals_total": 0,
        "entries": 0,
    }


def add_to_summary(summary: Dict[str, Any], log_type, status, quantity) -> None:
    """Fold one entry into a day summary."""
    qty = float(quantity) if quantity is not None else 0.0
    summary["entries"] += 1
    if log_type == "MEAL":
        summary["meals"] += 1
    elif log_type == "WATER":
        summary["water_ml"] += qty
    elif log_type == "ACTIVITY":
        if status != "SKIPPED":
            summary["activity_sessions"] += 1
            summary["activity_min"] += qty
    elif log_type == "MEDICATION":
        summary["meds_total"] += 1
        if status == "TAKEN":
            summary["meds_taken"] += 1
    elif log_type == "GOAL":
        summary["goals_total"] += 1
        if status == "ACHIEVED":
            summary["goals_achieved"] += 1


def _tidy(summary: Dict[str, Any]) -> Dict[str, Any]:
    summary["water_ml"] = _as_number(summary["water_ml"]) or 0
    summary["activity_min"] = _as_number(summary["activity_min"]) or 0
    return summary


def summarize(entries: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    summary = empty_summary()
    for entry in entries:
        add_to_summary(summary, entry["log_type"], entry.get("status"), entry.get("quantity"))
    return _tidy(summary)


def build_trend(rows, start: date, end: date) -> Dict[str, Any]:
    """Turn (log_date, log_type, status, quantity) rows into one point per day.

    Days without any entry are filled with zeros so the chart never has gaps.
    """
    days: Dict[date, Dict[str, Any]] = {}
    current = start
    while current <= end:
        days[current] = empty_summary()
        current += timedelta(days=1)

    totals = empty_summary()
    for log_date, log_type, status, quantity in rows:
        day = _as_date(log_date)
        if day not in days:
            continue
        add_to_summary(days[day], log_type, status, quantity)
        add_to_summary(totals, log_type, status, quantity)

    points = [{"log_date": day, **_tidy(summary)} for day, summary in days.items()]
    return {
        "points": points,
        "totals": _tidy(totals),
        "days_logged": sum(1 for point in points if point["entries"] > 0),
    }


# --------------------------------------------------------------------------- #
# Database access
# --------------------------------------------------------------------------- #
def find_current_plan_id(cursor, patient_id: int) -> Optional[int]:
    """The patient's CURRENT plan (SRS 3.2.3.2.2), or None if no plan exists."""
    try:
        cursor.execute(
            "SELECT plan_id FROM diet_plan "
            "WHERE patient_id = :patient_id AND status = 'CURRENT' "
            "ORDER BY created_at DESC, plan_id DESC FETCH FIRST 1 ROWS ONLY",
            patient_id=patient_id,
        )
    except oracledb.DatabaseError:
        # Database without the SCRUM-48 STATUS column: use the newest plan.
        cursor.execute(
            "SELECT plan_id FROM diet_plan WHERE patient_id = :patient_id "
            "ORDER BY created_at DESC, plan_id DESC FETCH FIRST 1 ROWS ONLY",
            patient_id=patient_id,
        )
    row = cursor.fetchone()
    return int(row[0]) if row else None


def read_dish_name(cursor, dish_id: int) -> Optional[str]:
    cursor.execute("SELECT name FROM dish_dna WHERE dish_id = :dish_id", dish_id=dish_id)
    row = cursor.fetchone()
    return row[0] if row else None


def water_total_for_day(
    cursor, patient_id: int, log_date: date, exclude_log_id: Optional[int] = None
) -> float:
    cursor.execute(
        "SELECT NVL(SUM(quantity), 0) FROM daily_log "
        "WHERE patient_id = :patient_id AND log_date = :log_date "
        "AND log_type = 'WATER' AND log_id <> :exclude_id",
        patient_id=patient_id,
        log_date=log_date,
        exclude_id=exclude_log_id or -1,
    )
    row = cursor.fetchone()
    return float(row[0] or 0) if row else 0.0


def read_entry_row(cursor, patient_id: int, log_id: int):
    cursor.execute(
        f"SELECT {LOG_COLS} FROM daily_log "
        "WHERE log_id = :log_id AND patient_id = :patient_id",
        log_id=log_id,
        patient_id=patient_id,
    )
    return cursor.fetchone()


def read_day_rows(cursor, patient_id: int, log_date: date):
    cursor.execute(
        f"SELECT {LOG_COLS} FROM daily_log "
        "WHERE patient_id = :patient_id AND log_date = :log_date "
        "ORDER BY log_time NULLS LAST, created_at, log_id",
        patient_id=patient_id,
        log_date=log_date,
    )
    return cursor.fetchall()


def read_range_rows(cursor, patient_id: int, start: date, end: date):
    cursor.execute(
        "SELECT log_date, log_type, status, quantity FROM daily_log "
        "WHERE patient_id = :patient_id "
        "AND log_date BETWEEN :start_date AND :end_date",
        patient_id=patient_id,
        start_date=start,
        end_date=end,
    )
    return cursor.fetchall()


def insert_entry(
    cursor, patient_id: int, plan_id: Optional[int], entry: DailyLogEntryIn, title: Optional[str]
) -> int:
    new_id = cursor.var(int)
    cursor.execute(
        """
        INSERT INTO daily_log (
            patient_id, plan_id, log_type, log_date, log_time, slot, title,
            dish_id, quantity, unit, status, notes
        ) VALUES (
            :patient_id, :plan_id, :log_type, :log_date, :log_time, :slot, :title,
            :dish_id, :quantity, :unit, :entry_status, :notes
        )
        RETURNING log_id INTO :new_id
        """,
        patient_id=patient_id,
        plan_id=plan_id,
        log_type=entry.log_type,
        log_date=entry.log_date,
        log_time=entry.log_time,
        slot=entry.slot,
        title=title,
        dish_id=entry.dish_id,
        quantity=entry.quantity,
        unit=entry.unit,
        entry_status=entry.status,
        notes=entry.notes,
        new_id=new_id,
    )
    value = new_id.getvalue()
    if isinstance(value, (list, tuple)):
        value = value[0]
    return int(value)


def update_entry(
    cursor,
    patient_id: int,
    log_id: int,
    entry: DailyLogEntryIn,
    title: Optional[str],
    plan_id: Optional[int],
) -> int:
    cursor.execute(
        """
        UPDATE daily_log SET
            plan_id = :plan_id,
            log_type = :log_type,
            log_date = :log_date,
            log_time = :log_time,
            slot = :slot,
            title = :title,
            dish_id = :dish_id,
            quantity = :quantity,
            unit = :unit,
            status = :entry_status,
            notes = :notes,
            updated_at = CURRENT_TIMESTAMP
        WHERE log_id = :log_id AND patient_id = :patient_id
        """,
        plan_id=plan_id,
        log_type=entry.log_type,
        log_date=entry.log_date,
        log_time=entry.log_time,
        slot=entry.slot,
        title=title,
        dish_id=entry.dish_id,
        quantity=entry.quantity,
        unit=entry.unit,
        entry_status=entry.status,
        notes=entry.notes,
        log_id=log_id,
        patient_id=patient_id,
    )
    return cursor.rowcount or 0


def delete_entry(cursor, patient_id: int, log_id: int) -> int:
    cursor.execute(
        "DELETE FROM daily_log WHERE log_id = :log_id AND patient_id = :patient_id",
        log_id=log_id,
        patient_id=patient_id,
    )
    return cursor.rowcount or 0