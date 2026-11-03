"""SCRUM-45: Pydantic schemas for the patient Health Profile (SRS 3.1.3.3).

Field placement follows the SRS ERD (Fig 5.7) and the agreed split:

Base patient fields -- stored in the PATIENT table (never duplicated into
HEALTH_PROFILE):
    age, gender, weight, height

Extended health-profile fields -- stored in the HEALTH_PROFILE table:
    conditions, allergies, activity_level, food_preference, goals,
    physical_limitations

Ownership & identity rules enforced by the router (see
backend.routers.health_profile_router):
    * The patient is identified STRICTLY from the authenticated JWT
      ``subject_id`` (role == "PATIENT"). No patient_id is accepted from the
      request body or URL for the /me routes.
    * GET is read-only. PUT upserts HEALTH_PROFILE and updates PATIENT within a
      single logical transaction, preserving the 1-to-1 PATIENT<->HEALTH_PROFILE
      relationship.

Every response carries the decision-support disclaimer text required by the SRS.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

# Required by the SRS: surfaced on every Health Profile API response so the
# system is never mistaken for a replacement for professional medical advice.
DECISION_SUPPORT_DISCLAIMER = (
    "DietRx is a decision-support system and does not replace professional "
    "medical advice."
)


# --------------------------------------------------------------------------- #
# Request body: PUT /api/health-profile/me
# --------------------------------------------------------------------------- #
class HealthProfileUpdate(BaseModel):
    """Fields a patient may update on their own profile.

    Base fields (age/gender/weight/height) are written to PATIENT; extended
    fields (conditions/allergies/activity_level/food_preference/goals/
    physical_limitations) are written to HEALTH_PROFILE.

    All fields are optional so the form can be submitted partially; omitted or
    empty-string values clear the corresponding nullable column rather than
    raising. This keeps the endpoint forgiving for a progressively-filled form.
    ``patient_id`` is deliberately NOT a field: identity comes from the token.
    """

    model_config = ConfigDict(extra="ignore")

    # Base patient fields -> PATIENT
    age: Optional[int] = Field(default=None, ge=0, le=150)
    gender: Optional[str] = Field(default=None, max_length=20)
    weight: Optional[float] = Field(default=None, gt=0)
    height: Optional[float] = Field(default=None, gt=0)

    # Extended fields -> HEALTH_PROFILE
    conditions: Optional[str] = Field(default=None, max_length=255)
    allergies: Optional[str] = Field(default=None, max_length=255)
    activity_level: Optional[str] = Field(default=None, max_length=50)
    food_preference: Optional[str] = Field(default=None, max_length=50)
    goals: Optional[str] = Field(default=None, max_length=255)
    physical_limitations: Optional[str] = Field(default=None, max_length=255)


# --------------------------------------------------------------------------- #
# Response: GET /api/health-profile/me
# --------------------------------------------------------------------------- #
class HealthProfileResponse(BaseModel):
    """Unified read model combining PATIENT (base) and HEALTH_PROFILE (extended).

    ``profile_exists`` is False when the patient has no HEALTH_PROFILE row yet;
    base patient fields are still returned from PATIENT. ``updated_at`` is None
    until a profile row is created. Always includes the disclaimer.
    """

    patient_id: int
    profile_exists: bool
    disclaimer: str = DECISION_SUPPORT_DISCLAIMER

    # Base patient fields (PATIENT)
    age: Optional[int] = None
    gender: Optional[str] = None
    weight: Optional[float] = None
    height: Optional[float] = None

    # Extended fields (HEALTH_PROFILE)
    conditions: Optional[str] = None
    allergies: Optional[str] = None
    activity_level: Optional[str] = None
    food_preference: Optional[str] = None
    goals: Optional[str] = None
    physical_limitations: Optional[str] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def build(
        cls,
        patient_id: int,
        patient_row: tuple | None,
        profile_row: tuple | None,
    ) -> "HealthProfileResponse":
        """Assemble the response from a PATIENT row and an optional profile row.

        ``patient_row`` is (age, gender, weight, height). ``profile_row`` is
        (conditions, allergies, activity_level, food_preference, goals,
        physical_limitations, updated_at) or None when no profile exists yet.
        """
        age = gender = weight = height = None
        if patient_row is not None:
            age, gender, weight, height = patient_row

        if profile_row is None:
            return cls(
                patient_id=patient_id,
                profile_exists=False,
                age=age,
                gender=gender,
                weight=weight,
                height=height,
            )

        (
            conditions,
            allergies,
            activity_level,
            food_preference,
            goals,
            physical_limitations,
            updated_at,
        ) = profile_row

        return cls(
            patient_id=patient_id,
            profile_exists=True,
            age=age,
            gender=gender,
            weight=weight,
            height=height,
            conditions=conditions,
            allergies=allergies,
            activity_level=activity_level,
            food_preference=food_preference,
            goals=goals,
            physical_limitations=physical_limitations,
            updated_at=updated_at,
        )
