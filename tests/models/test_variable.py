"""Tests for VariableSpec."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from op_aromic.models.base import KIND_REGISTRY
from op_aromic.models.variable import VariableEntry, VariableSpec


def test_variable_minimal_static() -> None:
    spec = VariableSpec(var_type="STATIC", entries=[VariableEntry(key="K", value="V")])
    assert spec.var_type == "STATIC"
    assert spec.entries[0].key == "K"


def test_variable_defaults_to_static() -> None:
    spec = VariableSpec(entries=[])
    assert spec.var_type == "STATIC"


def test_variable_var_type_constrained() -> None:
    with pytest.raises(ValidationError):
        VariableSpec(var_type="BOGUS", entries=[])


def test_variable_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        VariableSpec(entries=[], unexpected=True)  # type: ignore[call-arg]


def test_variable_raw_escape_hatch() -> None:
    spec = VariableSpec(entries=[], raw={"OH_VAR_SRC": "DB"})
    assert spec.raw == {"OH_VAR_SRC": "DB"}


def test_variable_entry_requires_key() -> None:
    with pytest.raises(ValidationError):
        VariableEntry(value="v")  # type: ignore[call-arg]


def test_variable_registered_in_kind_registry() -> None:
    assert KIND_REGISTRY["Variable"] is VariableSpec
