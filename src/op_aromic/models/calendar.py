"""CalendarSpec — Automic CALE (calendar) spec."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from op_aromic.models.base import KIND_REGISTRY

CalendarKeywordType = Literal["STATIC", "WEEKDAY", "MONTHLY", "YEARLY", "ROLL"]


class CalendarKeyword(BaseModel):
    """A single keyword inside a Calendar; semantics vary by `type`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, Field(min_length=1)]
    type: CalendarKeywordType = "STATIC"
    values: list[str] = Field(default_factory=list)


class CalendarSpec(BaseModel):
    """Spec body for a Calendar (CALE)."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    keywords: list[CalendarKeyword] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


KIND_REGISTRY["Calendar"] = CalendarSpec


__all__ = ["CalendarKeyword", "CalendarKeywordType", "CalendarSpec"]
