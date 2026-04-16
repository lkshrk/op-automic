"""JobSpec — Automic JOBS (job) spec."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from op_aromic.models.base import KIND_REGISTRY

JobScriptType = Literal["OS", "SQL", "PS"]


class JobSpec(BaseModel):
    """Spec body for a Job (JOBS).

    Models only the common subset used across OS/SQL/PS jobs. Unmodelled
    Automic attributes go through `raw`.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    host: Annotated[str, Field(min_length=1)]
    login: Annotated[str, Field(min_length=1)]
    script: Annotated[str, Field(min_length=1)]
    script_type: JobScriptType = "OS"
    raw: dict[str, Any] = Field(default_factory=dict)


KIND_REGISTRY["Job"] = JobSpec


__all__ = ["JobScriptType", "JobSpec"]
