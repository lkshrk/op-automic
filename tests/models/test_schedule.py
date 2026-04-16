"""Tests for ScheduleSpec."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from op_aromic.models.base import KIND_REGISTRY, ObjectRef
from op_aromic.models.schedule import ScheduleEntry, ScheduleSpec


def test_schedule_minimal() -> None:
    spec = ScheduleSpec(entries=[])
    assert spec.entries == []


def test_schedule_with_entry() -> None:
    spec = ScheduleSpec(
        entries=[
            ScheduleEntry(
                task=ObjectRef(kind="Workflow", name="DAILY.LOAD"),
                start_time="02:00",
                calendar_keyword="WORKDAY",
            ),
        ],
    )
    assert spec.entries[0].task.name == "DAILY.LOAD"
    assert spec.entries[0].start_time == "02:00"


def test_schedule_entry_requires_task() -> None:
    with pytest.raises(ValidationError):
        ScheduleEntry(start_time="02:00")  # type: ignore[call-arg]


def test_schedule_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ScheduleSpec(entries=[], unexpected=True)  # type: ignore[call-arg]


def test_schedule_raw_escape_hatch() -> None:
    spec = ScheduleSpec(entries=[], raw={"OH_SCHED": "Y"})
    assert spec.raw == {"OH_SCHED": "Y"}


def test_schedule_start_time_format_validated() -> None:
    with pytest.raises(ValidationError):
        ScheduleEntry(
            task=ObjectRef(kind="Workflow", name="W"),
            start_time="25:99",
        )


def test_schedule_registered_in_kind_registry() -> None:
    assert KIND_REGISTRY["Schedule"] is ScheduleSpec
