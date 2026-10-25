"""SCRUM-59 request bodies."""

from pydantic import BaseModel, Field


class SwapIn(BaseModel):
    """Save a swap: buy ``to_item`` instead of ``from_item`` (grocery keys or names)."""

    from_item: str = Field(min_length=1, max_length=60)
    to_item: str = Field(min_length=1, max_length=60)
