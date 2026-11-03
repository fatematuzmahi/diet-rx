"""SCRUM-57: request models for budgets and prices (SRS 3.3.3.1).

Error messages are written so the page can show them directly.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.services.price_service import BUDGET_MODES

MAX_BUDGET = 1_000_000
SEASONS = ("ALL_YEAR", "VARIES", "WINTER", "SUMMER", "MONSOON")


class BudgetIn(BaseModel):
    """Body of PUT /api/budget/me."""

    mode: str
    amount: float
    plan_days: Optional[int] = None
    preferred_market: Optional[str] = Field(default=None, max_length=100)

    @field_validator("mode", mode="before")
    @classmethod
    def _mode(cls, value):
        text = str(value or "").strip().upper()
        if text not in BUDGET_MODES:
            raise ValueError("Budget type must be daily, weekly, monthly or plan.")
        return text

    @field_validator("preferred_market", mode="before")
    @classmethod
    def _market(cls, value):
        text = str(value or "").strip()
        return text or None

    @model_validator(mode="after")
    def _rules(self):
        if not (0 < self.amount <= MAX_BUDGET):
            raise ValueError(f"Budget must be more than 0 and at most {MAX_BUDGET:,} BDT.")
        if self.mode == "PLAN":
            if self.plan_days is None or not (1 <= self.plan_days <= 31):
                raise ValueError("For a plan budget, enter the plan length: 1 to 31 days.")
        else:
            self.plan_days = None
        self.amount = round(float(self.amount), 2)
        return self


class PriceIn(BaseModel):
    """Body of POST /api/prices and PUT /api/prices/{price_id} (admin update path)."""

    item_name: str = Field(min_length=1, max_length=150)
    category: Optional[str] = Field(default=None, max_length=40)
    unit: str = Field(min_length=1, max_length=50)
    price_bdt: float
    market: Optional[str] = Field(default="Dhaka retail markets", max_length=100)
    source: str = Field(min_length=1, max_length=200)
    source_url: Optional[str] = Field(default=None, max_length=400)
    price_date: Optional[date] = None
    season: Optional[str] = "ALL_YEAR"
    season_note: Optional[str] = Field(default=None, max_length=300)

    @field_validator("item_name", "unit", "source", "market", "category", "season_note", mode="before")
    @classmethod
    def _strip(cls, value):
        if value is None:
            return None
        return str(value).strip()

    @field_validator("season", mode="before")
    @classmethod
    def _season(cls, value):
        text = str(value or "ALL_YEAR").strip().upper().replace(" ", "_")
        if text not in SEASONS:
            raise ValueError("Season must be all year, varies, winter, summer or monsoon.")
        return text

    @field_validator("source_url", mode="before")
    @classmethod
    def _url(cls, value):
        text = str(value or "").strip()
        if text and not text.startswith("https://"):
            raise ValueError("Source link must start with https://")
        return text or None

    @model_validator(mode="after")
    def _rules(self):
        if not self.item_name or not self.unit or not self.source:
            raise ValueError("Item name, serving unit and source are required.")
        if not (0 < self.price_bdt <= 99_999_999.99):
            raise ValueError("Price must be more than 0 BDT.")
        if self.price_date is None:
            self.price_date = date.today()
        if self.price_date > date.today():
            raise ValueError("Price date can't be in the future.")
        self.price_bdt = round(float(self.price_bdt), 2)
        return self
