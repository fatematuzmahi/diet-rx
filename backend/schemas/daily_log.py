"""SCRUM-53: Daily Logging schemas (SRS 3.2.3.2).

One request model (``DailyLogEntryIn``) is used for both creating and
correcting an entry, so the same validation rules apply to new and edited
logs. Every rule raises a plain-language message the UI can show directly
(SRS usability: "error messages must explain what went wrong and what the
user can do next").
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

LOG_TYPES = ("MEAL", "WATER", "ACTIVITY", "MEDICATION", "GOAL")
MEAL_SLOTS = ("BREAKFAST", "LUNCH", "DINNER", "SNACK")
ACTIVITY_STATUSES = ("COMPLETED", "MODIFIED", "SKIPPED")
MEDICATION_STATUSES = ("TAKEN", "MISSED", "SKIPPED")
GOAL_STATUSES = ("ACHIEVED", "IN_PROGRESS", "MISSED")

UNITS = {
    "MEAL": "serving",
    "WATER": "ml",
    "ACTIVITY": "min",
    "MEDICATION": "dose",
    "GOAL": None,
}

MAX_BACKFILL_DAYS = 30
MAX_WATER_ML_PER_ENTRY = 3000
MAX_WATER_ML_PER_DAY = 10000
MAX_ACTIVITY_MIN_PER_ENTRY = 600
MAX_MEAL_SERVINGS = 10
MAX_MEDICATION_DOSES = 20

DISCLAIMER = (
    "DietRx is a decision-support system and does not replace professional "
    "medical advice. Daily logs are self-reported by the user."
)

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _today() -> date:
    """Today's date (a function so tests can freeze it)."""
    return date.today()


def check_log_date(value: date) -> None:
    """Reject future dates and dates outside the editable window."""
    today = _today()
    if value > today:
        raise ValueError("You can't log entries for a future date.")
    if value < today - timedelta(days=MAX_BACKFILL_DAYS):
        raise ValueError(
            f"Entries older than {MAX_BACKFILL_DAYS} days can't be added or changed."
        )


class DailyLogEntryIn(BaseModel):
    """Body of POST /api/daily-log and PUT /api/daily-log/{log_id}."""

    log_type: str
    log_date: Optional[date] = None
    log_time: Optional[str] = None
    slot: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=150)
    dish_id: Optional[int] = Field(default=None, ge=1)
    quantity: Optional[float] = None
    status: Optional[str] = None
    notes: Optional[str] = Field(default=None, max_length=500)

    @field_validator("log_type", "slot", "status", mode="before")
    @classmethod
    def _normalise_code(cls, value):
        if value is None:
            return None
        text = str(value).strip().upper().replace(" ", "_").replace("-", "_")
        return text or None

    @field_validator("title", "notes", "log_time", mode="before")
    @classmethod
    def _strip_text(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="after")
    def _apply_rules(self):
        if self.log_type not in LOG_TYPES:
            raise ValueError(
                "Log type must be one of: meal, water, activity, medication, goal."
            )

        if self.log_date is None:
            self.log_date = _today()
        check_log_date(self.log_date)

        if self.log_time is not None and not _TIME_RE.match(self.log_time):
            raise ValueError("Time must be in HH:MM format, for example 08:30.")

        q = self.quantity
        kind = self.log_type

        if kind == "MEAL":
            if not self.title and self.dish_id is None:
                raise ValueError("Tell us what you ate: type a food name or pick a dish.")
            if self.slot not in MEAL_SLOTS:
                raise ValueError(
                    "Choose which meal this was: breakfast, lunch, dinner or snack."
                )
            if q is not None and not (0 < q <= MAX_MEAL_SERVINGS):
                raise ValueError(
                    f"Servings must be more than 0 and at most {MAX_MEAL_SERVINGS}."
                )
            self.status = None

        elif kind == "WATER":
            if q is None:
                raise ValueError("Enter how much water you drank, in ml.")
            if not (1 <= q <= MAX_WATER_ML_PER_ENTRY):
                raise ValueError(
                    f"One water entry must be between 1 and {MAX_WATER_ML_PER_ENTRY} ml. "
                    "Log larger amounts as separate entries."
                )
            self.title = self.title or "Water"
            self.slot = None
            self.status = None
            self.dish_id = None

        elif kind == "ACTIVITY":
            if not self.title:
                raise ValueError("Name the activity, for example Brisk walking.")
            self.status = self.status or "COMPLETED"
            if self.status not in ACTIVITY_STATUSES:
                raise ValueError("Mark the activity as Completed, Modified or Skipped.")
            if self.status == "SKIPPED":
                if q is not None and not (0 <= q <= MAX_ACTIVITY_MIN_PER_ENTRY):
                    raise ValueError(
                        f"Duration must be between 0 and {MAX_ACTIVITY_MIN_PER_ENTRY} minutes."
                    )
            else:
                if q is None:
                    raise ValueError("Enter how many minutes the activity lasted.")
                if not (1 <= q <= MAX_ACTIVITY_MIN_PER_ENTRY):
                    raise ValueError(
                        f"Duration must be between 1 and {MAX_ACTIVITY_MIN_PER_ENTRY} minutes."
                    )
            self.slot = None
            self.dish_id = None

        elif kind == "MEDICATION":
            if not self.title:
                raise ValueError("Enter the medicine name.")
            if self.status not in MEDICATION_STATUSES:
                raise ValueError("Mark the medicine as Taken, Missed or Skipped.")
            if q is not None and not (1 <= q <= MAX_MEDICATION_DOSES):
                raise ValueError(
                    f"Doses must be between 1 and {MAX_MEDICATION_DOSES}."
                )
            self.slot = None
            self.dish_id = None

        elif kind == "GOAL":
            if not self.title:
                raise ValueError("Describe the goal, for example No sugary drinks.")
            if self.status not in GOAL_STATUSES:
                raise ValueError("Mark the goal as Achieved, In progress or Missed.")
            self.quantity = None
            self.slot = None
            self.dish_id = None

        return self

    @property
    def unit(self) -> Optional[str]:
        return UNITS.get(self.log_type)


class DailyLogEntryOut(BaseModel):
    log_id: int
    log_type: str
    log_date: date
    log_time: Optional[str] = None
    slot: Optional[str] = None
    title: Optional[str] = None
    dish_id: Optional[int] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    plan_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DaySummary(BaseModel):
    water_ml: float = 0
    meals: int = 0
    activity_min: float = 0
    activity_sessions: int = 0
    meds_taken: int = 0
    meds_total: int = 0
    goals_achieved: int = 0
    goals_total: int = 0
    entries: int = 0


class DailyLogDayResponse(BaseModel):
    log_date: date
    plan_id: Optional[int] = None
    editable: bool = True
    entries: List[DailyLogEntryOut] = []
    summary: DaySummary = DaySummary()
    disclaimer: str = DISCLAIMER


class DailyTrendPoint(DaySummary):
    log_date: date


class DailyLogTrendResponse(BaseModel):
    days: int
    start_date: date
    end_date: date
    days_logged: int = 0
    points: List[DailyTrendPoint] = []
    totals: DaySummary = DaySummary()


class DailyLogDeleteResponse(BaseModel):
    log_id: int
    deleted: bool = True