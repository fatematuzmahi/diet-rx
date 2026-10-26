"""SCRUM-56: progress sharing + professional review (SRS 3.2.3.5).

Two tables (``backend/database/apply_sharing_scrum56.py``):

    SHARE_PERMISSION  one row per patient + doctor: which of Daily Log, plan
                      and progress that doctor may see. All three off means
                      STATUS = 'REVOKED'. Rows are never deleted.
    PLAN_REVIEW       insert-only record of who reviewed which plan, the
                      decision, the note and when (SRS 3.2.3.5.4).

Rules
-----
* The patient decides. A doctor sees ONLY the parts the patient switched on
  (SRS 3.2.3.5.2); everything else is refused with 403 by the router.
* Escalation (SRS 3.2.3.5.3): a CURRENT plan with ``requires_review = 1``
  appears in the review list of every doctor the patient shares the plan with.
* Every function takes explicit ids. The routers take those ids from the
  verified access token, never from the request body.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from backend.schemas.plan import loads_plan_part
from backend.services.daily_log_service import LOG_COLS

#: The three things a patient can share, mapped to their SHARE_PERMISSION column.
SHARE_ITEMS = {
    "daily_log": "share_daily_log",
    "plan": "share_plan",
    "progress": "share_progress",
}
ITEM_LABELS = {"daily_log": "Daily Log", "plan": "diet plan", "progress": "progress"}

STATUS_ACTIVE = "ACTIVE"
STATUS_REVOKED = "REVOKED"

SHARE_COLS = (
    "s.share_id, s.doctor_id, d.name, d.specialization, "
    "s.share_daily_log, s.share_plan, s.share_progress, s.status, s.updated_at"
)
REVIEW_COLS = "r.review_id, r.plan_id, r.patient_id, r.doctor_id, d.name, r.decision, r.note, r.reviewed_at"


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def flags_from(payload) -> Dict[str, bool]:
    return {item: bool(getattr(payload, item)) for item in SHARE_ITEMS}


def status_for(flags: Dict[str, bool]) -> str:
    return STATUS_ACTIVE if any(flags.values()) else STATUS_REVOKED


def row_to_share(row) -> Dict[str, Any]:
    (share_id, doctor_id, doctor_name, specialization,
     daily_log, plan, progress, status, updated_at) = row
    shares = {"daily_log": bool(daily_log), "plan": bool(plan), "progress": bool(progress)}
    active = status == STATUS_ACTIVE and any(shares.values())
    return {
        "share_id": int(share_id),
        "doctor": {"doctor_id": int(doctor_id), "name": doctor_name, "specialization": specialization},
        "shares": shares,
        "status": STATUS_ACTIVE if active else STATUS_REVOKED,
        "updated_at": updated_at,
    }


def row_to_review(row) -> Dict[str, Any]:
    review_id, plan_id, patient_id, doctor_id, doctor_name, decision, note, reviewed_at = row
    return {
        "review_id": int(review_id),
        "plan_id": int(plan_id),
        "patient_id": int(patient_id),
        "doctor": {"doctor_id": int(doctor_id), "name": doctor_name},
        "decision": decision,
        "note": note,
        "reviewed_at": reviewed_at,
    }


def can_see(permission: Optional[Dict[str, Any]], item: str) -> bool:
    """True only when the patient's ACTIVE permission switches this item on."""
    if not permission or permission.get("status") != STATUS_ACTIVE:
        return False
    return bool(permission.get(item))


def review_status(requires_review: bool, reviews: Sequence[Dict[str, Any]]) -> str:
    """not_needed | pending | approved | changes_needed. The newest review wins."""
    if reviews:
        return "approved" if reviews[0]["decision"] == "APPROVED" else "changes_needed"
    return "pending" if requires_review else "not_needed"


def conflict_titles(conflict_notes) -> List[str]:
    titles = []
    for entry in loads_plan_part(conflict_notes) or []:
        if isinstance(entry, dict) and entry.get("title"):
            titles.append(str(entry["title"]))
    return titles


def _as_date(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


# --------------------------------------------------------------------------- #
# Doctors (patient side)
# --------------------------------------------------------------------------- #
def list_doctors(cursor) -> List[Dict[str, Any]]:
    """Doctors who can log in. Rows kept only as prescription references are skipped."""
    cursor.execute(
        "SELECT doctor_id, name, specialization FROM doctor "
        "WHERE password_hash IS NOT NULL ORDER BY name, doctor_id"
    )
    return [{"doctor_id": int(r[0]), "name": r[1], "specialization": r[2]}
            for r in cursor.fetchall() or []]


def read_doctor(cursor, doctor_id: int) -> Optional[Dict[str, Any]]:
    cursor.execute(
        "SELECT doctor_id, name, specialization FROM doctor "
        "WHERE doctor_id = :doctor_id AND password_hash IS NOT NULL",
        doctor_id=doctor_id,
    )
    row = cursor.fetchone()
    return {"doctor_id": int(row[0]), "name": row[1], "specialization": row[2]} if row else None


# --------------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------------- #
def read_patient_shares(cursor, patient_id: int) -> List[Dict[str, Any]]:
    cursor.execute(
        f"SELECT {SHARE_COLS} FROM share_permission s "
        "JOIN doctor d ON d.doctor_id = s.doctor_id "
        "WHERE s.patient_id = :patient_id ORDER BY d.name, s.share_id",
        patient_id=patient_id,
    )
    return [row_to_share(r) for r in cursor.fetchall() or []]


def save_share(cursor, patient_id: int, doctor_id: int, flags: Dict[str, bool]) -> str:
    """Insert or update the patient+doctor row. Returns the new STATUS."""
    status = status_for(flags)
    cursor.execute(
        """
        MERGE INTO share_permission t
        USING (SELECT :patient_id AS patient_id, :doctor_id AS doctor_id FROM dual) src
        ON (t.patient_id = src.patient_id AND t.doctor_id = src.doctor_id)
        WHEN MATCHED THEN UPDATE SET
            t.share_daily_log = :daily_log, t.share_plan = :plan,
            t.share_progress = :progress, t.status = :status, t.updated_at = :updated_at
        WHEN NOT MATCHED THEN INSERT
            (patient_id, doctor_id, share_daily_log, share_plan, share_progress, status, updated_at)
            VALUES (src.patient_id, src.doctor_id, :daily_log, :plan, :progress, :status, :updated_at)
        """,
        patient_id=patient_id,
        doctor_id=doctor_id,
        daily_log=1 if flags["daily_log"] else 0,
        plan=1 if flags["plan"] else 0,
        progress=1 if flags["progress"] else 0,
        status=status,
        updated_at=now(),
    )
    return status


def read_permission(cursor, patient_id: int, doctor_id: int) -> Optional[Dict[str, Any]]:
    """What this doctor may see of this patient, or None if nothing was ever shared."""
    cursor.execute(
        "SELECT share_daily_log, share_plan, share_progress, status FROM share_permission "
        "WHERE patient_id = :patient_id AND doctor_id = :doctor_id",
        patient_id=patient_id,
        doctor_id=doctor_id,
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {"daily_log": bool(row[0]), "plan": bool(row[1]), "progress": bool(row[2]), "status": row[3]}


def read_doctor_patients(cursor, doctor_id: int) -> List[Dict[str, Any]]:
    """Patients with an ACTIVE share to this doctor (name only, no contact details)."""
    cursor.execute(
        "SELECT s.patient_id, p.name, s.share_daily_log, s.share_plan, s.share_progress, s.updated_at "
        "FROM share_permission s JOIN patient p ON p.patient_id = s.patient_id "
        "WHERE s.doctor_id = :doctor_id AND s.status = 'ACTIVE' ORDER BY p.name, s.patient_id",
        doctor_id=doctor_id,
    )
    patients = []
    for patient_id, name, daily_log, plan, progress, updated_at in cursor.fetchall() or []:
        shares = {"daily_log": bool(daily_log), "plan": bool(plan), "progress": bool(progress)}
        if any(shares.values()):
            patients.append({"patient_id": int(patient_id), "name": name, "shares": shares,
                             "shared_since": updated_at})
    return patients


# --------------------------------------------------------------------------- #
# Plans and reviews
# --------------------------------------------------------------------------- #
def read_current_plan_brief(cursor, patient_id: int) -> Optional[Dict[str, Any]]:
    cursor.execute(
        "SELECT plan_id, requires_review, created_at, conflict_notes FROM diet_plan "
        "WHERE patient_id = :patient_id AND status = 'CURRENT' "
        "ORDER BY created_at DESC, plan_id DESC FETCH FIRST 1 ROWS ONLY",
        patient_id=patient_id,
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {"plan_id": int(row[0]), "requires_review": bool(row[1]), "generated_at": row[2],
            "conflicts": conflict_titles(row[3])}


def read_plan_owner(cursor, plan_id: int) -> Optional[Dict[str, Any]]:
    cursor.execute(
        "SELECT plan_id, patient_id, status, requires_review FROM diet_plan WHERE plan_id = :plan_id",
        plan_id=plan_id,
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {"plan_id": int(row[0]), "patient_id": int(row[1]), "status": row[2],
            "requires_review": bool(row[3])}


def read_plan_reviews(cursor, plan_id: int) -> List[Dict[str, Any]]:
    cursor.execute(
        f"SELECT {REVIEW_COLS} FROM plan_review r JOIN doctor d ON d.doctor_id = r.doctor_id "
        "WHERE r.plan_id = :plan_id ORDER BY r.reviewed_at DESC, r.review_id DESC",
        plan_id=plan_id,
    )
    return [row_to_review(r) for r in cursor.fetchall() or []]


def read_patient_reviews(cursor, patient_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    cursor.execute(
        f"SELECT {REVIEW_COLS} FROM plan_review r JOIN doctor d ON d.doctor_id = r.doctor_id "
        "WHERE r.patient_id = :patient_id ORDER BY r.reviewed_at DESC, r.review_id DESC",
        patient_id=patient_id,
    )
    return [row_to_review(r) for r in (cursor.fetchall() or [])[:limit]]


def insert_review(cursor, plan_id: int, patient_id: int, doctor_id: int,
                  decision: str, note: Optional[str]) -> int:
    new_id = cursor.var(int)
    cursor.execute(
        "INSERT INTO plan_review (plan_id, patient_id, doctor_id, decision, note, reviewed_at) "
        "VALUES (:plan_id, :patient_id, :doctor_id, :decision, :note, :reviewed_at) "
        "RETURNING review_id INTO :new_id",
        plan_id=plan_id,
        patient_id=patient_id,
        doctor_id=doctor_id,
        decision=decision,
        note=note,
        reviewed_at=now(),
        new_id=new_id,
    )
    value = new_id.getvalue()
    return int(value[0] if isinstance(value, (list, tuple)) else value)


def read_review_queue(cursor, doctor_id: int) -> List[Dict[str, Any]]:
    """CURRENT plans that need a professional review, from patients who share the plan."""
    cursor.execute(
        "SELECT p.plan_id, p.patient_id, pt.name, p.created_at, p.conflict_notes "
        "FROM diet_plan p "
        "JOIN share_permission s ON s.patient_id = p.patient_id "
        "JOIN patient pt ON pt.patient_id = p.patient_id "
        "WHERE s.doctor_id = :doctor_id AND s.status = 'ACTIVE' AND s.share_plan = 1 "
        "AND p.status = 'CURRENT' AND p.requires_review = 1 "
        "ORDER BY p.created_at, p.plan_id",
        doctor_id=doctor_id,
    )
    rows = cursor.fetchall() or []
    queue = []
    for plan_id, patient_id, name, created_at, conflict_notes in rows:
        reviews = read_plan_reviews(cursor, int(plan_id))
        queue.append({
            "plan_id": int(plan_id),
            "patient": {"patient_id": int(patient_id), "name": name},
            "generated_at": created_at,
            "conflicts": conflict_titles(conflict_notes),
            "review_status": review_status(True, reviews),
            "latest_review": reviews[0] if reviews else None,
        })
    # Waiting reviews first, then the rest; oldest first inside each group.
    queue.sort(key=lambda item: item["review_status"] != "pending")
    return queue


# --------------------------------------------------------------------------- #
# Daily Log entries for the doctor view
# --------------------------------------------------------------------------- #
def read_log_entries(cursor, patient_id: int, start: date, end: date):
    cursor.execute(
        f"SELECT {LOG_COLS} FROM daily_log "
        "WHERE patient_id = :patient_id AND log_date BETWEEN :start_date AND :end_date "
        "ORDER BY log_date DESC, log_time NULLS LAST, created_at, log_id",
        patient_id=patient_id,
        start_date=start,
        end_date=end,
    )
    return cursor.fetchall() or []
