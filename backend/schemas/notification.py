"""SCRUM-51: Smart Notification & Alerting schemas (SRS 3.1.3.4).

Error messages are written so the UI can show them directly.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

REMINDER_TYPES = ("MEAL", "WATER", "MEDICATION", "EXERCISE", "GOAL")
DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
ACK_ACTIONS = ("DONE", "DISMISSED")
MAX_REMINDERS = 30

DISCLAIMER = (
    "DietRx reminders and alerts are decision support only. They are not a "
    "medical diagnosis or emergency advice. If you feel unwell, contact your "
    "doctor or local emergency services."
)

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def normalise_days(value) -> List[str]:
    """Accept a list or comma text; 'DAILY' / 'EVERYDAY' mean all seven days."""
    if value is None:
        return list(DAYS)
    items = value.split(",") if isinstance(value, str) else list(value)
    days = []
    for item in items:
        code = str(item).strip().upper()[:3]
        if not code:
            continue
        if code in ("DAI", "EVE", "ALL"):
            return list(DAYS)
        if code not in DAYS:
            raise ValueError("Repeat days must be Mon, Tue, Wed, Thu, Fri, Sat or Sun.")
        if code not in days:
            days.append(code)
    if not days:
        raise ValueError("Pick at least one day for this reminder.")
    return [d for d in DAYS if d in days]


class ReminderIn(BaseModel):
    """Body of POST /api/notifications/reminders and PUT .../{id}."""

    reminder_type: str
    title: str = Field(..., max_length=150)
    message: Optional[str] = Field(default=None, max_length=300)
    remind_time: str
    repeat_days: Optional[List[str]] = None
    enabled: bool = True

    @field_validator("reminder_type", mode="before")
    @classmethod
    def _type(cls, value):
        text = str(value or "").strip().upper()
        if text == "ACTIVITY":
            text = "EXERCISE"
        if text not in REMINDER_TYPES:
            raise ValueError("Reminder type must be meal, water, medication, exercise or goal.")
        return text

    @field_validator("title", "message", mode="before")
    @classmethod
    def _strip(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("remind_time", mode="before")
    @classmethod
    def _time(cls, value):
        text = str(value or "").strip()
        if len(text) == 8 and text.count(":") == 2:  # HH:MM:SS from <input type=time>
            text = text[:5]
        if not _TIME_RE.match(text):
            raise ValueError("Time must be in HH:MM format, for example 08:30.")
        return text

    @field_validator("repeat_days", mode="before")
    @classmethod
    def _days(cls, value):
        return normalise_days(value)

    @model_validator(mode="after")
    def _rules(self):
        if not self.title:
            raise ValueError("Give the reminder a short title, for example Take iron tablet.")
        if self.repeat_days is None:
            self.repeat_days = list(DAYS)
        return self


class ReminderOut(BaseModel):
    notification_id: int
    reminder_type: str
    title: str
    message: Optional[str] = None
    remind_time: str
    repeat_days: List[str]
    enabled: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ReminderListResponse(BaseModel):
    reminders: List[ReminderOut] = []
    total: int = 0
    max_reminders: int = MAX_REMINDERS


class FeedItem(BaseModel):
    event_id: int
    category: str
    reminder_type: Optional[str] = None
    severity: str = "INFO"
    title: str
    message: Optional[str] = None
    event_date: date
    delivered_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    ack_action: Optional[str] = None
    notification_id: Optional[int] = None


class FeedResponse(BaseModel):
    feed_date: date
    feed_time: str
    items: List[FeedItem] = []
    unread: int = 0
    disclaimer: str = DISCLAIMER


class AckIn(BaseModel):
    action: str = "DONE"

    @field_validator("action", mode="before")
    @classmethod
    def _action(cls, value):
        text = str(value or "DONE").strip().upper()
        if text not in ACK_ACTIONS:
            raise ValueError("Action must be done or dismissed.")
        return text


class StatsResponse(BaseModel):
    days: int
    delivered: int = 0
    done: int = 0
    dismissed: int = 0
    pending: int = 0
    done_rate: int = 0
    by_type: dict = {}
    safety_alerts: int = 0


class DeleteResponse(BaseModel):
    notification_id: int
    deleted: bool = True