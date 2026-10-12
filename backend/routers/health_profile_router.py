"""SCRUM-45: Patient Health Profile endpoints (SRS 3.1.3.3, ERD Fig 5.7).

Endpoints
---------
    GET  /api/health-profile/me   -- read the current patient's profile
    PUT  /api/health-profile/me   -- create or update the current patient's profile

Identity & ownership
--------------------
The patient is identified STRICTLY from the authenticated access token's
``subject_id`` (``Principal.subject``) with ``role == "PATIENT"``. These routes
accept NO ``patient_id`` in the path, query string, or request body, so a
patient can only ever read or update their OWN profile. The existing SCRUM-44
security layer is reused unchanged:

    * 401 -- missing / malformed / expired / bad-signature / wrong-purpose token.
    * 403 -- an authenticated principal that is not a PATIENT (e.g. a DOCTOR).

Data placement (per SRS ERD + agreed split)
-------------------------------------------
Base patient fields (age, gender, weight, height) are stored in PATIENT.
Extended fields (conditions, allergies, activity_level, food_preference, goals,
physical_limitations) are stored in HEALTH_PROFILE. Base fields are never
duplicated into HEALTH_PROFILE.

PUT performs an UPSERT: if the patient has no HEALTH_PROFILE row yet one is
INSERTed; otherwise the single existing row is UPDATED. The PATIENT base-field
update and the HEALTH_PROFILE upsert run inside one logical transaction and are
committed together (or rolled back together), preserving the 1-to-1 relationship.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from backend.core.security import Principal, require_patient
from backend.database.connection import get_connection
from backend.schemas.health_profile import (
    HealthProfileResponse,
    HealthProfileUpdate,
)

router = APIRouter(prefix="/api/health-profile", tags=["Health Profile"])


def _require_connection():
    """Return a live Oracle connection or raise 503 (distinct from 401/403)."""
    connection = get_connection()
    if connection is None:
        raise HTTPException(status_code=503, detail="Database connection failed")
    return connection


def _patient_pk(principal: Principal) -> int:
    """Coerce the authenticated patient subject_id to the PATIENT primary key.

    Identity comes only from the verified token, never from the request. A token
    whose subject is not a valid integer id is treated as an invalid credential.
    """
    try:
        return int(principal.subject)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has an invalid subject.",
        )


# --------------------------------------------------------------------------- #
# GET /api/health-profile/me
# --------------------------------------------------------------------------- #
@router.get("/me", response_model=HealthProfileResponse)
def read_my_health_profile(
    principal: Principal = Depends(require_patient),
):
    """Read the authenticated patient's own health profile (READ-ONLY).

    Returns base patient fields from PATIENT plus extended fields from
    HEALTH_PROFILE. If no HEALTH_PROFILE row exists yet, ``profile_exists`` is
    False and extended fields are null while base fields are still returned.
    """

    patient_id = _patient_pk(principal)

    connection = _require_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT age, gender, weight, height
            FROM patient
            WHERE patient_id = :patient_id
            """,
            patient_id=patient_id,
        )
        patient_row = cursor.fetchone()
        if patient_row is None:
            # The token verified but no such patient exists (deleted account).
            # 401 keeps a stale token from reading anything.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authenticated patient no longer exists.",
            )

        cursor.execute(
            """
            SELECT conditions, allergies, activity_level, food_preference,
                   goals, physical_limitations, updated_at
            FROM health_profile
            WHERE patient_id = :patient_id
            """,
            patient_id=patient_id,
        )
        profile_row = cursor.fetchone()

        return HealthProfileResponse.build(patient_id, patient_row, profile_row)
    finally:
        cursor.close()
        connection.close()


# --------------------------------------------------------------------------- #
# PUT /api/health-profile/me
# --------------------------------------------------------------------------- #
@router.put("/me", response_model=HealthProfileResponse)
def update_my_health_profile(
    payload: HealthProfileUpdate,
    principal: Principal = Depends(require_patient),
):
    """Create or update the authenticated patient's own health profile.

    Updates base patient fields in PATIENT and upserts extended fields in
    HEALTH_PROFILE within a single transaction. Identity is taken only from the
    token's subject_id; the request body cannot target another patient.
    """

    patient_id = _patient_pk(principal)

    connection = _require_connection()
    cursor = connection.cursor()
    try:
        # 1) Confirm the patient still exists (token verified, row may be gone).
        cursor.execute(
            "SELECT 1 FROM patient WHERE patient_id = :patient_id",
            patient_id=patient_id,
        )
        if cursor.fetchone() is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authenticated patient no longer exists.",
            )

        # 2) Update base patient fields in PATIENT (only provided values).
        base_updates = {
            "age": payload.age,
            "gender": payload.gender,
            "weight": payload.weight,
            "height": payload.height,
        }
        base_setters = {
            column: value
            for column, value in base_updates.items()
            if value is not None
        }
        if base_setters:
            assignments = ", ".join(f"{column} = :{column}" for column in base_setters)
            cursor.execute(
                f"UPDATE patient SET {assignments} WHERE patient_id = :patient_id",
                **base_setters,
                patient_id=patient_id,
            )

        # 3) Upsert the single HEALTH_PROFILE row for this patient.
        extended = {
            "conditions": payload.conditions,
            "allergies": payload.allergies,
            "activity_level": payload.activity_level,
            "food_preference": payload.food_preference,
            "goals": payload.goals,
            "physical_limitations": payload.physical_limitations,
        }

        cursor.execute(
            "SELECT profile_id FROM health_profile WHERE patient_id = :patient_id",
            patient_id=patient_id,
        )
        existing = cursor.fetchone()

        if existing is None:
            # INSERT: always set patient_id; set extended columns that are provided.
            columns = ["patient_id"]
            placeholders = [":patient_id"]
            params: dict = {"patient_id": patient_id}
            for column, value in extended.items():
                if value is not None:
                    columns.append(column)
                    placeholders.append(f":{column}")
                    params[column] = value
            columns.append("updated_at")
            placeholders.append("CURRENT_TIMESTAMP")
            cursor.execute(
                f"INSERT INTO health_profile ({', '.join(columns)}) "
                f"VALUES ({', '.join(placeholders)})",
                **params,
            )
        else:
            # UPDATE the existing 1-to-1 row; always refresh updated_at.
            setters = ["updated_at = CURRENT_TIMESTAMP"]
            update_params: dict = {}
            for column, value in extended.items():
                if value is not None:
                    setters.append(f"{column} = :{column}")
                    update_params[column] = value
            update_params["patient_id"] = patient_id
            cursor.execute(
                f"UPDATE health_profile SET {', '.join(setters)} "
                "WHERE patient_id = :patient_id",
                **update_params,
            )

        # 4) Commit the whole logical transaction (PATIENT + HEALTH_PROFILE).
        connection.commit()

        # 5) Re-read the authoritative state to return a consistent response.
        cursor.execute(
            "SELECT age, gender, weight, height FROM patient WHERE patient_id = :patient_id",
            patient_id=patient_id,
        )
        patient_row = cursor.fetchone()
        cursor.execute(
            """
            SELECT conditions, allergies, activity_level, food_preference,
                   goals, physical_limitations, updated_at
            FROM health_profile
            WHERE patient_id = :patient_id
            """,
            patient_id=patient_id,
        )
        profile_row = cursor.fetchone()

        return HealthProfileResponse.build(patient_id, patient_row, profile_row)
    except HTTPException:
        connection.rollback()
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

