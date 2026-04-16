"""ScheduleSpec — Automic JSCH (schedule) spec."""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from op_aromic.models.base import KIND_REGISTRY, ObjectRef

# HH:MM (24h), no seconds.
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class ScheduleEntry(BaseModel):
    """A single schedule execution entry: task + when."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task: ObjectRef
    start_time: Annotated[str, Field(min_length=1)] = "00:00"
    calendar_keyword: str | None = None

    @field_validator("start_time")
    @classmethod
    def _validate_start_time(cls, v: str) -> str:
        if not _TIME_RE.match(v):
            raise ValueError(f"start_time must match HH:MM 24h format, got {v!r}")
        return v


class ScheduleSpec(BaseModel):
    """Spec body for a Schedule (JSCH)."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    entries: list[ScheduleEntry] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


KIND_REGISTRY["Schedule"] = ScheduleSpec


__all__ = ["ScheduleEntry", "ScheduleSpec"]
