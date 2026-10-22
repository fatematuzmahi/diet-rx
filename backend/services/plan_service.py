"""SCRUM-48: persistence for the Personalized Diet + Activity Plan.

The generated plan is stored in the existing DIET_PLAN / ACTIVITY_PLAN tables
(extended by ``backend/database/personalized_plan_scrum48.sql``):

    DIET_PLAN
        plan_id, patient_id, prescription_id, calorie_target,
        meal_schedule, created_at                -- base columns (schema.sql)
        status, meal_plan, activity_plan, profile_snapshot,
        restrictions_applied, conflict_notes, requires_review, updated_at
                                                -- SCRUM-48 columns

    ACTIVITY_PLAN
        activity_plan_id, patient_id, plan_id, exercise_list,
        duration, intensity                      -- base columns (schema.sql)
        status, activity_plan, cautions, updated_at
                                                -- SCRUM-48 columns

SRS 3.3.6.5 requires that the CURRENT accepted plan is stored so Module 2 can
monitor adherence. Regenerating therefore keeps the previous plan as history:
the old row is flipped to ``SUPERSEDED`` and a NEW row becomes ``CURRENT``.
Only one plan per patient is ``CURRENT`` at any time.

Identity
--------
Every function here takes an explicit ``patient_id``. The caller (the router)
is responsible for deriving that id from the verified access token -- this
module never accepts an id from an untrusted source itself.

The plan body lives in CLOB columns as JSON. The single place where the
mapping between the JSON and the API payload is defined is
``backend.schemas.plan`` (``dumps_plan_part`` / ``loads_plan_part``), so the
stored document and the returned payload can never drift apart.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from backend.schemas.plan import (
    STATUS_CURRENT,
    STATUS_SUPERSEDED,
    PlanActivity,
    PlanConflict,
    PlanDay,
    dumps_plan_part,
    loads_plan_part,
)

logger = logging.getLogger(__name__)

#: Columns of a DIET_PLAN row, in the order used by ``row_to_plan``.
_PLAN_COLS = (
    "plan_id, patient_id, calorie_target, meal_schedule, created_at, "
    "status, meal_plan, activity_plan, profile_snapshot, "
    "restrictions_applied, conflict_notes, requires_review"
)

#: Columns of an ACTIVITY_PLAN row, in the order used by ``_activities_from_rows``.
_ACTIVITY_COLS = (
    "activity_plan_id, patient_id, plan_id, exercise_list, duration, "
    "intensity, status, activity_plan, cautions"
)


def _utc_now_naive() -> datetime:
    """UTC now compatible with Oracle TIMESTAMP (no tzinfo).

    Matches the convention used by the SCRUM-44/45/46 routers.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_text(value: Any) -> Optional[str]:
    """Read a VARCHAR2/CLOB value as text.

    Oracle returns a CLOB as a LOB object whose content is only reachable via
    ``read()``; ``str()`` is the fallback for plain strings.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    read = getattr(value, "read", None)
    if callable(read):
        try:
            return str(read())
        except Exception:  # pragma: no cover - defensive, LOB read failure
            return None
    return str(value)



# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def read_plan_row(cursor, plan_id: int):
    """Fetch one DIET_PLAN row by id, or None."""
    cursor.execute(
        f"SELECT {_PLAN_COLS} FROM diet_plan WHERE plan_id = :plan_id",
        plan_id=plan_id,
    )
    return cursor.fetchone()


def read_current_plan_row(cursor, patient_id: int):
    """Fetch the patient's CURRENT DIET_PLAN row, or None.

    The ORDER BY makes the result deterministic even if more than one CURRENT
    row somehow exists (for example after a manual database edit): the most
    recently created plan wins.
    """
    cursor.execute(
        f"SELECT {_PLAN_COLS} FROM diet_plan "
        "WHERE patient_id = :patient_id AND status = :status "
        "ORDER BY created_at DESC, plan_id DESC",
        patient_id=patient_id,
        status=STATUS_CURRENT,
    )
    return cursor.fetchone()


def read_plan_history_rows(cursor, patient_id: int, limit: int = 20):
    """Fetch the patient's plans, newest first."""
    cursor.execute(
        f"SELECT {_PLAN_COLS} FROM diet_plan "
        "WHERE patient_id = :patient_id "
        "ORDER BY created_at DESC, plan_id DESC",
        patient_id=patient_id,
    )
    return cursor.fetchall()[: max(1, min(int(limit or 20), 100))]


def read_activity_rows(cursor, plan_id: int):
    """Fetch the ACTIVITY_PLAN rows that belong to a plan."""
    cursor.execute(
        f"SELECT {_ACTIVITY_COLS} FROM activity_plan "
        "WHERE plan_id = :plan_id ORDER BY activity_plan_id",
        plan_id=plan_id,
    )
    return cursor.fetchall()


def read_prescription_rows(cursor, patient_id: int):
    """Fetch the patient's prescriptions as ``{restrictions, source}`` dicts.

    Only the columns the conflict resolver needs are read. The rows are
    returned newest-first so the most recent instruction is the last one
    applied.
    """
    cursor.execute(
        "SELECT restrictions, source FROM prescription "
        "WHERE patient_id = :patient_id ORDER BY upload_date DESC, "
        "prescription_id DESC",
        patient_id=patient_id,
    )
    prescriptions: List[Dict[str, Any]] = []
    for restrictions, source in cursor.fetchall():
        text = _to_text(restrictions)
        if not text or not str(text).strip():
            continue
        prescriptions.append(
            {
                "restrictions": str(text),
                "source": source or "Prescription",
            }
        )
    return prescriptions



# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def insert_plan(
    cursor,
    patient_id: int,
    *,
    calorie_target: Optional[int],
    meal_plan: Sequence[Dict],
    activity_plan: Sequence[Dict],
    cautions: Sequence[str],
    restrictions_applied: Sequence[str],
    conflicts: Sequence[Dict],
    profile_snapshot: Dict,
    prescription_id: Optional[int] = None,
    created_at: Optional[datetime] = None,
) -> int:
    """Insert a new DIET_PLAN row (plus its ACTIVITY_PLAN row) and return the id.

    PLAN_ID is a GENERATED ALWAYS AS IDENTITY column: Oracle allocates the
    value itself, so the column is deliberately NOT in the column list and no
    sequence is referenced. The generated key is read back with
    ``RETURNING ... INTO``; ``cursor.var(int).getvalue()`` returns a one-element
    LIST, so the id is the first element.
    """
    now = created_at or _utc_now_naive()

    plan_id_var = cursor.var(int)
    cursor.execute(
        """
        INSERT INTO diet_plan (
            patient_id, prescription_id, calorie_target, meal_schedule,
            created_at, status, meal_plan, activity_plan, profile_snapshot,
            restrictions_applied, conflict_notes, requires_review, updated_at
        ) VALUES (
            :patient_id, :prescription_id, :calorie_target, :meal_schedule,
            :created_at, :status, :meal_plan, :activity_plan, :profile_snapshot,
            :restrictions_applied, :conflict_notes, :requires_review, :updated_at
        )
        RETURNING plan_id INTO :new_id
        """,
        patient_id=patient_id,
        prescription_id=prescription_id,
        calorie_target=calorie_target,
        meal_schedule=_meal_schedule_summary(meal_plan),
        created_at=now,
        status=STATUS_CURRENT,
        meal_plan=dumps_plan_part(list(meal_plan)),
        activity_plan=dumps_plan_part(list(activity_plan)),
        profile_snapshot=dumps_plan_part(profile_snapshot),
        restrictions_applied=dumps_plan_part(list(restrictions_applied)),
        conflict_notes=dumps_plan_part(list(conflicts)),
        requires_review=1 if conflicts and _needs_review(conflicts) else 0,
        updated_at=now,
        new_id=plan_id_var,
    )
    plan_id = _read_generated_id(plan_id_var)

    # The matching ACTIVITY_PLAN row keeps the base table (exercise_list,
    # duration, intensity) populated as well as the JSON body.
    first_activity = activity_plan[0] if activity_plan else {}
    cursor.execute(
        """
        INSERT INTO activity_plan (
            patient_id, plan_id, exercise_list, duration, intensity,
            status, activity_plan, cautions, updated_at
        ) VALUES (
            :patient_id, :plan_id, :exercise_list, :duration, :intensity,
            :status, :activity_plan, :cautions, :updated_at
        )
        """,
        patient_id=patient_id,
        plan_id=plan_id,
        exercise_list=_exercise_list_summary(activity_plan),
        duration=first_activity.get("duration_minutes"),
        intensity=first_activity.get("intensity"),
        status=STATUS_CURRENT,
        activity_plan=dumps_plan_part(list(activity_plan)),
        cautions=dumps_plan_part(list(cautions)),
        updated_at=now,
    )
    return plan_id


def supersede_previous_plans(cursor, patient_id: int) -> int:
    """Flip the patient's CURRENT plans to SUPERSEDED; return how many rows changed.

    Both DIET_PLAN and ACTIVITY_PLAN are updated so the "current plan" lookup
    can never find an orphaned activity row.
    """
    now = _utc_now_naive()
    cursor.execute(
        "UPDATE diet_plan SET status = :status, updated_at = :updated_at "
        "WHERE patient_id = :patient_id AND status = :current",
        status=STATUS_SUPERSEDED,
        updated_at=now,
        patient_id=patient_id,
        current=STATUS_CURRENT,
    )
    diet_rows = cursor.rowcount or 0

    cursor.execute(
        "UPDATE activity_plan SET status = :status, updated_at = :updated_at "
        "WHERE patient_id = :patient_id AND status = :current",
        status=STATUS_SUPERSEDED,
        updated_at=now,
        patient_id=patient_id,
        current=STATUS_CURRENT,
    )
    activity_rows = cursor.rowcount or 0
    return diet_rows + activity_rows


def _read_generated_id(plan_id_var) -> int:
    """Read the generated key out of an Oracle ``cursor.var(int)``."""
    value = plan_id_var.getvalue()
    if isinstance(value, (list, tuple)):
        return int(value[0])


# --------------------------------------------------------------------------- #
# Mapping rows -> API payloads
# --------------------------------------------------------------------------- #
def _as_plan_days(raw) -> List[PlanDay]:
    """Build the typed day list from the stored JSON."""
    days: List[PlanDay] = []
    for index, entry in enumerate(loads_plan_part(raw) or []):
        if not isinstance(entry, dict):
            continue
        entry.setdefault("day", index + 1)
        try:
            days.append(PlanDay.model_validate(entry))
        except Exception:  # pragma: no cover - defensive, a corrupt day is skipped
            continue
    return days


def _as_plan_activities(raw) -> List[PlanActivity]:
    """Build the typed activity list from the stored JSON."""
    activities: List[PlanActivity] = []
    for entry in loads_plan_part(raw) or []:
        if not isinstance(entry, dict):
            continue
        try:
            activities.append(PlanActivity.model_validate(entry))
        except Exception:  # pragma: no cover - defensive
            continue
    return activities


def _as_plan_conflicts(raw) -> List[PlanConflict]:
    """Build the typed conflict list from the stored JSON."""
    conflicts: List[PlanConflict] = []
    for entry in loads_plan_part(raw) or []:
        if not isinstance(entry, dict):
            continue
        try:
            conflicts.append(PlanConflict.model_validate(entry))
        except Exception:  # pragma: no cover - defensive
            continue
    return conflicts


def conflicts_from_row(row) -> List[PlanConflict]:
    """Return the typed conflicts of a DIET_PLAN row.

    The conflict JSON lives in the 11th column of ``_PLAN_COLS``
    (``conflict_notes``). This is the public accessor for the history
    endpoint, which only needs the conflict COUNT.
    """
    if not row:
        return []
    return _as_plan_conflicts(row[10])


def row_to_plan(cursor, row) -> Dict[str, Any]:
    """Map a DIET_PLAN row (+ its activity rows) to the API payload.

    The returned dict is the exact body of ``PlanGenerateResponse`` /
    ``PlanCurrentResponse``, so what the API returns is what was persisted.
    """
    (
        plan_id,
        patient_id,
        calorie_target,
        meal_schedule,
        created_at,
        status,
        meal_plan,
        activity_plan,
        profile_snapshot,
        restrictions_applied,
        conflict_notes,
        requires_review,
    ) = row

    stored_activities = _as_plan_activities(activity_plan)
    if not stored_activities:
        stored_activities = _activities_from_rows(read_activity_rows(cursor, plan_id))

    snapshot = loads_plan_part(profile_snapshot)
    if not isinstance(snapshot, dict):
        snapshot = {}

    return {
        "plan_id": int(plan_id),
        "patient_id": int(patient_id),
        "status": status or STATUS_CURRENT,
        "generated_at": created_at,
        "calorie_target": int(calorie_target) if calorie_target is not None else None,
        "days": _as_plan_days(meal_plan),
        "activities": stored_activities,
        "restrictions_applied": loads_plan_part(restrictions_applied) or [],
        "conflicts": _as_plan_conflicts(conflict_notes),
        "requires_review": bool(requires_review),
        "budget_notes": snapshot.get("budget_notes", []) if isinstance(snapshot, dict) else [],
        "notes": snapshot.get("notes", []) if isinstance(snapshot, dict) else [],
    }


def _activities_from_rows(activity_rows) -> List[PlanActivity]:
    """Rebuild the activity list from the ACTIVITY_PLAN rows.

    Used only as a fallback for plans stored before the JSON body existed.
    """
    activities: List[PlanActivity] = []
    for row in activity_rows or []:
        (
            activity_plan_id,
            _patient_id,
            _plan_id,
            exercise_list,
            duration,
            intensity,
            _status,
            activity_json,
            _cautions,
        ) = row
        stored = _as_plan_activities(activity_json)
        if stored:
            activities.extend(stored)
            continue
        for name in str(exercise_list or "").split(","):
            name = name.strip()
            if not name:
                continue
            activities.append(
                PlanActivity(name=name, duration_minutes=duration, intensity=intensity)
            )
    return activities

    return int(value)


def _needs_review(conflicts: Sequence[Dict]) -> bool:
    """True when any conflict must be reviewed by a professional."""
    return any(bool(conflict.get("requires_review")) for conflict in conflicts)


def _meal_schedule_summary(meal_plan: Sequence[Dict]) -> str:
    """Build the short ``MEAL_SCHEDULE`` summary (max 255 chars)."""
    if not meal_plan:
        return ""
    parts = []
    for meal in meal_plan:
        meal_type = str(meal.get("meal_type", ""))[:40]
        time = str(meal.get("time", ""))[:10]
        parts.append(f"{meal_type} {time}".strip())
    return ", ".join(parts)[:255]


def _exercise_list_summary(activity_plan: Sequence[Dict]) -> str:
    """Build the short ``EXERCISE_LIST`` summary (max 255 chars)."""
    if not activity_plan:
        return ""
    names = [str(activity.get("name", ""))[:60] for activity in activity_plan]
    return ", ".join(names)[:255]
