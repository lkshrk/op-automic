"""VariableSpec — Automic VARA (variable) spec."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from op_aromic.models.base import KIND_REGISTRY

VariableType = Literal["STATIC", "DYNAMIC", "SQL", "EXEC"]


class VariableEntry(BaseModel):
    """A single key/value row inside a Variable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: Annotated[str, Field(min_length=1)]
    value: str = ""


class VariableSpec(BaseModel):
    """Spec body for a Variable (VARA)."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    var_type: VariableType = "STATIC"
    entries: list[VariableEntry] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


KIND_REGISTRY["Variable"] = VariableSpec


__all__ = ["VariableEntry", "VariableSpec", "VariableType"]
