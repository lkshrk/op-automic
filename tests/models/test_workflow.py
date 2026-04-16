"""Tests for WorkflowSpec."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from op_aromic.models.base import KIND_REGISTRY, ObjectRef
from op_aromic.models.workflow import WorkflowSpec, WorkflowTask


def test_workflow_minimal() -> None:
    spec = WorkflowSpec(title="Daily ETL", tasks=[])
    assert spec.title == "Daily ETL"
    assert spec.tasks == []


def test_workflow_with_tasks_and_after() -> None:
    spec = WorkflowSpec(
        title="ETL",
        tasks=[
            WorkflowTask(name="STEP.EXTRACT", ref=ObjectRef(kind="Job", name="ETL.EXTRACT")),
            WorkflowTask(
                name="STEP.LOAD",
                ref=ObjectRef(kind="Job", name="ETL.LOAD"),
                after=["STEP.EXTRACT"],
            ),
        ],
    )
    assert len(spec.tasks) == 2
    assert spec.tasks[1].after == ["STEP.EXTRACT"]


def test_workflow_task_requires_name_and_ref() -> None:
    with pytest.raises(ValidationError):
        WorkflowTask()  # type: ignore[call-arg]


def test_workflow_spec_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        WorkflowSpec(title="x", tasks=[], unexpected=True)  # type: ignore[call-arg]


def test_workflow_raw_escape_hatch() -> None:
    spec = WorkflowSpec(title="x", tasks=[], raw={"OH_SOMETHING": 42})
    assert spec.raw == {"OH_SOMETHING": 42}


def test_workflow_duplicate_task_names_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkflowSpec(
            title="x",
            tasks=[
                WorkflowTask(name="STEP.A", ref=ObjectRef(kind="Job", name="A")),
                WorkflowTask(name="STEP.A", ref=ObjectRef(kind="Job", name="B")),
            ],
        )


def test_workflow_after_must_reference_declared_task() -> None:
    with pytest.raises(ValidationError):
        WorkflowSpec(
            title="x",
            tasks=[
                WorkflowTask(
                    name="STEP.A",
                    ref=ObjectRef(kind="Job", name="A"),
                    after=["NOT.DECLARED"],
                ),
            ],
        )


def test_workflow_registered_in_kind_registry() -> None:
    assert KIND_REGISTRY["Workflow"] is WorkflowSpec
