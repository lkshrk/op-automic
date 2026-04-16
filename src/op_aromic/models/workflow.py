"""WorkflowSpec — Automic JOBP (workflow) spec."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from op_aromic.models.base import KIND_REGISTRY, ObjectRef


class WorkflowTask(BaseModel):
    """A single node inside a Workflow. `after` is a list of sibling task names."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, Field(min_length=1)]
    ref: ObjectRef
    after: list[str] = Field(default_factory=list)


class WorkflowSpec(BaseModel):
    """Spec body for a Workflow (JOBP)."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    tasks: list[WorkflowTask] = Field(default_factory=list)
    # Escape hatch for fields we haven't formally modelled; passed through verbatim.
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_task_graph(self) -> WorkflowSpec:
        names = [t.name for t in self.tasks]
        if len(names) != len(set(names)):
            raise ValueError("duplicate WorkflowTask names")
        declared = set(names)
        for task in self.tasks:
            for dep in task.after:
                if dep not in declared:
                    raise ValueError(
                        f"task {task.name!r} depends on undeclared task {dep!r}",
                    )
        return self


KIND_REGISTRY["Workflow"] = WorkflowSpec


__all__ = ["WorkflowSpec", "WorkflowTask"]
