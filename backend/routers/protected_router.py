"""SCRUM-44: Role-based protected endpoints (FastAPI only).

These endpoints exist to make role-based access control observable and to guard
the patient health / prescription / professional-review functions the SRS names.

Access control (all enforced in backend.core.security):
  * 401 -- no / malformed / expired / bad-signature / wrong-purpose token.
  * 403 -- authenticated but lacking the required role, OR a PATIENT principal
           trying to read a DIFFERENT patient's data.

Professional-review is intentionally a SAFE STUB. The current schema has no
PATIENT <-> DOCTOR link, so there is no stored notion of "this doctor's
patients". Rather than expose every patient's records to any logged-in doctor,
the endpoint returns an empty result with an explicit reason. This must stay
empty until a patient<->doctor relationship is designed and approved.
"""

from fastapi import APIRouter, Depends, Query

from backend.core.security import (
    Principal,
    require_authenticated,
    require_doctor,
    require_self_or_doctor,
)
from backend.database.connection import get_connection

router = APIRouter(prefix="/api/protected", tags=["Protected (RBAC)"])


def _require_connection():
    connection = get_connection()
    if connection is None:
        # 503 keeps a DB outage distinct from an authorization failure.
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Database connection failed")
    return connection


@router.get("/me")
def who_am_i(principal: Principal = Depends(require_authenticated)):
    """Echo the authenticated principal. Proves the token verified (401 if not)."""

    return {
        "role": principal.role,
        "subject": principal.subject,
        "message": "Authenticated.",
    }


@router.get("/health/{patient_id}")
def get_health_profile(
    patient_id: int,
    principal: Principal = Depends(require_self_or_doctor),
):
    """A patient's own health profile, or a doctor's view.

    401 without a valid token; 403 when a PATIENT asks for someone else.
    READ-ONLY.
    """

    connection = _require_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT conditions, allergies, activity_level, updated_at
            FROM health_profile
            WHERE patient_id = :patient_id
            """,
            patient_id=patient_id,
        )
        row = cursor.fetchone()
        if row is None:
            return {
                "patient_id": patient_id,
                "profile": None,
                "message": "No health profile found for this patient.",
            }
        conditions, allergies, activity_level, updated_at = row
        return {
            "patient_id": patient_id,
            "profile": {
                "conditions": conditions,
                "allergies": allergies,
                "activity_level": activity_level,
                "updated_at": str(updated_at) if updated_at else None,
            },
        }
    finally:
        cursor.close()
        connection.close()


@router.get("/prescriptions/{patient_id}")
def get_prescriptions(
    patient_id: int,
    principal: Principal = Depends(require_self_or_doctor),
):
    """A patient's own prescriptions, or a doctor's view.

    Returns metadata only (never secret content). READ-ONLY.
    """

    connection = _require_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT prescription_id, doctor_id, upload_date, validated
            FROM prescription
            WHERE patient_id = :patient_id
            ORDER BY upload_date DESC
            """,
            patient_id=patient_id,
        )
        items = [
            {
                "prescription_id": prescription_id,
                "doctor_id": doctor_id,
                "upload_date": str(upload_date) if upload_date else None,
                "validated": bool(validated),
            }
            for prescription_id, doctor_id, upload_date, validated in cursor.fetchall()
        ]
        return {"patient_id": patient_id, "count": len(items), "prescriptions": items}
    finally:
        cursor.close()
        connection.close()


@router.get("/professional-review")
def professional_review(
    principal: Principal = Depends(require_doctor),
):
    """Doctor-only professional review queue.

    401 without a token; 403 for any PATIENT token.

    SAFE STUB: returns an empty queue because the schema has no PATIENT<->DOCTOR
    link, so no doctor can be scoped to a set of patients. This MUST remain
    empty (rather than return all patients) until such a link is designed and
    approved. No patient-identifying data is returned here.
    """

    return {
        "role": principal.role,
        "review_queue": [],
        "message": (
            "Professional review is not yet linked to patients: the current "
            "schema has no patient<->doctor relationship, so an empty queue is "
            "returned instead of exposing all patient records."
        ),
    }
