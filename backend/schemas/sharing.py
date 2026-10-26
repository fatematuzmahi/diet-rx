"""SCRUM-56 request bodies: progress sharing + professional review (SRS 3.2.3.5)."""

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

DECISIONS = ("APPROVED", "CHANGES_NEEDED")


class ShareIn(BaseModel):
    """What the patient lets one doctor see. All three off = sharing turned off."""

    doctor_id: int = Field(..., gt=0)
    daily_log: bool = False
    plan: bool = False
    progress: bool = False


class ReviewIn(BaseModel):
    """A doctor's review of a shared plan."""

    plan_id: int = Field(..., gt=0)
    decision: Literal["APPROVED", "CHANGES_NEEDED"]
    note: Optional[str] = Field(None, max_length=1000)

    @field_validator("note")
    @classmethod
    def _tidy_note(cls, value):
        if value is None:
            return None
        value = " ".join(value.split())
        return value or None

    @model_validator(mode="after")
    def _changes_need_a_note(self):
        if self.decision == "CHANGES_NEEDED" and not self.note:
            raise ValueError("Please write what should change in the plan.")
        return self
