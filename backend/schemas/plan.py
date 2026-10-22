"""SCRUM-48: Pydantic schemas for the Personalized Diet + Activity Plan.

Implements the SRS 3.3.6 requirements:

    3.3.6.1 generate a personalized MEAL plan from profile, prescription,
            conditions, preferences, goals and budget context;
    3.3.6.2 the plan respects known dietary restrictions and unresolved
            safety conflicts;
    3.3.6.3 diet guidance is combined with activity guidance;
    3.3.6.4 the user can regenerate a plan after changing goals/preferences;
    3.3.6.5 the current accepted plan is stored so Module 2 can monitor
            adherence.

The plan body is stored as JSON in CLOB columns (DIET_PLAN.MEAL_PLAN,
DIET_PLAN.ACTIVITY_PLAN, ...). These models are the canonical in-memory shape
of that JSON, so what the API returns is exactly what was persisted.

Every response carries the SRS decision-support disclaimer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

#: Surfaced on every plan response (SRS decision-support notice).
DECISION_SUPPORT_DISCLAIMER = (
    "DietRx is a decision-support system and does not replace professional "
    "medical advice. Final clinical decisions remain with qualified "
    "healthcare professionals."
)

#: Additional caution required by SRS 3.3.3.5 for activity guidance.
ACTIVITY_PROFESSIONAL_CAUTION = (
    "If you have an uncertain or high-risk health condition, or you are "
    "unsure whether an activity is safe for you, consult a qualified "
    "healthcare professional before starting it."
)

#: Plan lifecycle status (SCRUM-48 migration, DIET_PLAN.STATUS).
STATUS_CURRENT = "CURRENT"
STATUS_SUPERSEDED = "SUPERSEDED"


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #
class PlanMeal(BaseModel):
    """One generated meal inside the daily meal plan."""

    model_config = ConfigDict(extra="ignore")

    meal_type: str = Field(max_length=40, examples=["Breakfast"])
    time: str = Field(max_length=20, examples=["08:00"])
    items: List[str] = Field(default_factory=list)
    calories: Optional[int] = Field(default=None, ge=0)
    protein_g: Optional[float] = Field(default=None, ge=0)
    carbs_g: Optional[float] = Field(default=None, ge=0)
    fat_g: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = Field(default=None, max_length=255)


class PlanDay(BaseModel):
    """One day of the generated meal plan."""

    model_config = ConfigDict(extra="ignore")

    day: int = Field(ge=1, le=31, examples=[1])
    label: Optional[str] = Field(default=None, max_length=60)
    meals: List[PlanMeal] = Field(default_factory=list)
    daily_calories: Optional[int] = Field(default=None, ge=0)
    notes: Optional[str] = Field(default=None, max_length=255)


class PlanActivity(BaseModel):
    """One generated activity suggestion (SRS 3.3.3)."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(max_length=120, examples=["Brisk walking"])
    description: Optional[str] = Field(default=None, max_length=500)
    duration_minutes: Optional[int] = Field(default=None, ge=0, le=1440)
    intensity: Optional[str] = Field(default=None, max_length=50)
    frequency: Optional[str] = Field(default=None, max_length=80)
    days: List[str] = Field(default_factory=list)
    cautions: List[str] = Field(default_factory=list)


class PlanConflict(BaseModel):
    """A detected conflict between dietary rules (SRS 3.3.4)."""

    model_config = ConfigDict(extra="ignore")

    code: str = Field(max_length=60, examples=["contradictory_restrictions"])
    title: str = Field(max_length=160)
    explanation: str = Field(max_length=1000)
    severity: str = Field(default="medium", max_length=20)
    sources: List[str] = Field(default_factory=list)
    resolution: Optional[str] = Field(default=None, max_length=1000)
    requires_review: bool = False


# --------------------------------------------------------------------------- #
# Request body
# --------------------------------------------------------------------------- #
class PlanGenerateRequest(BaseModel):
    """Optional overrides for a plan generation request (SRS 3.3.6.4).

    Every field is optional: when omitted, the value stored in the patient's
    health profile / prescription is used. Sending a value lets the patient
    regenerate a plan against changed goals, preferences or health information
    WITHOUT first editing the profile.
    """

    model_config = ConfigDict(extra="ignore")

    days: int = Field(default=7, ge=1, le=31)
    regenerate: bool = Field(
        default=True,
        description=(
            "True (default) supersedes the patient's current plan and stores "
            "the new one as CURRENT. False creates an additional plan row "
            "without demoting the current one."
        ),
    )
    goals: Optional[str] = Field(default=None, max_length=255)
    food_preference: Optional[str] = Field(default=None, max_length=50)
    activity_level: Optional[str] = Field(default=None, max_length=50)
    monthly_budget: Optional[float] = Field(default=None, gt=0)


# --------------------------------------------------------------------------- #
# Response bodies
# --------------------------------------------------------------------------- #
class PlanGenerateResponse(BaseModel):
    """Result of generating (or regenerating) a plan."""

    model_config = ConfigDict(extra="ignore")

    plan_id: int
    patient_id: int
    status: str = STATUS_CURRENT
    generated_at: Optional[datetime] = None
    calorie_target: Optional[int] = None
    days: List[PlanDay] = Field(default_factory=list)
    activities: List[PlanActivity] = Field(default_factory=list)
    restrictions_applied: List[str] = Field(default_factory=list)
    conflicts: List[PlanConflict] = Field(default_factory=list)
    requires_review: bool = False
    budget_notes: List[str] = Field(default_factory=list)
    notes: List[str] = Field(
        default_factory=list,
        description="Plain-language notes explaining how the plan was adjusted.",
    )
    disclaimer: str = DECISION_SUPPORT_DISCLAIMER
    activity_caution: str = ACTIVITY_PROFESSIONAL_CAUTION


class PlanSummary(BaseModel):
    """Lightweight entry used by the plan history list."""

    model_config = ConfigDict(extra="ignore")

    plan_id: int
    status: str
    created_at: Optional[datetime] = None
    calorie_target: Optional[int] = None
    requires_review: bool = False
    conflict_count: int = 0


class PlanHistoryResponse(BaseModel):
    """The patient's plan history (previous + current plans)."""

    model_config = ConfigDict(extra="ignore")

    patient_id: int
    plans: List[PlanSummary] = Field(default_factory=list)
    total: int = 0
    disclaimer: str = DECISION_SUPPORT_DISCLAIMER


class PlanCurrentResponse(PlanGenerateResponse):
    """The patient's stored CURRENT plan.

    ``found`` is False when the patient has never generated a plan; the rest
    of the fields are then empty rather than missing.
    """

    model_config = ConfigDict(extra="ignore")

    found: bool = False


# --------------------------------------------------------------------------- #
# Persistence helpers
# --------------------------------------------------------------------------- #
# The plan body lives in CLOB columns as JSON. These helpers are the single
# place where the mapping between the Pydantic models and that JSON is
# defined, so the stored document and the API payload can never drift apart.


def _clob_to_text(value: Any) -> Optional[str]:
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


def dumps_plan_part(part: Any) -> Optional[str]:
    """Serialise one plan section (meals, activities, conflicts, ...).

    ``None`` is returned for an empty section so the CLOB stays NULL instead
    of holding a meaningless empty array.
    """
    if part is None:
        return None
    if hasattr(part, "model_dump"):
        part = part.model_dump()
    if isinstance(part, (list, tuple, dict)) and not part:
        return None
    import json

    return json.dumps(part, default=str)


def loads_plan_part(raw: Any) -> Any:
    """Parse one stored plan section back into Python data.

    A corrupt/empty CLOB never raises: it degrades to an empty list so the
    plan can still be displayed.
    """
    text = _clob_to_text(raw)
    if not text:
        return []
    import json

    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return []
