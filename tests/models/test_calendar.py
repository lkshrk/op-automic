"""Tests for CalendarSpec."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from op_aromic.models.base import KIND_REGISTRY
from op_aromic.models.calendar import CalendarKeyword, CalendarSpec


def test_calendar_minimal() -> None:
    spec = CalendarSpec(keywords=[])
    assert spec.keywords == []


def test_calendar_with_keyword() -> None:
    spec = CalendarSpec(
        keywords=[
            CalendarKeyword(name="WORKDAY", type="WEEKDAY", values=["MON", "TUE", "WED"]),
        ],
    )
    assert spec.keywords[0].name == "WORKDAY"


def test_calendar_keyword_requires_name() -> None:
    with pytest.raises(ValidationError):
        CalendarKeyword(type="WEEKDAY")  # type: ignore[call-arg]


def test_calendar_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CalendarSpec(keywords=[], unexpected=True)  # type: ignore[call-arg]


def test_calendar_raw_escape_hatch() -> None:
    spec = CalendarSpec(keywords=[], raw={"OH_CAL_TYPE": "STATIC"})
    assert spec.raw == {"OH_CAL_TYPE": "STATIC"}


def test_calendar_keyword_type_constrained() -> None:
    with pytest.raises(ValidationError):
        CalendarKeyword(name="X", type="BOGUS")


def test_calendar_registered_in_kind_registry() -> None:
    assert KIND_REGISTRY["Calendar"] is CalendarSpec
