"""Tests for JobSpec."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from op_aromic.models.base import KIND_REGISTRY
from op_aromic.models.job import JobSpec


def test_job_minimal() -> None:
    spec = JobSpec(host="HOST.WIN", login="LOGIN.ADMIN", script="echo hi")
    assert spec.host == "HOST.WIN"
    assert spec.login == "LOGIN.ADMIN"
    assert spec.script == "echo hi"


def test_job_title_optional() -> None:
    spec = JobSpec(title="nightly", host="H", login="L", script="s")
    assert spec.title == "nightly"


def test_job_script_type_defaults_to_os() -> None:
    spec = JobSpec(host="H", login="L", script="s")
    assert spec.script_type == "OS"


def test_job_script_type_constrained() -> None:
    with pytest.raises(ValidationError):
        JobSpec(host="H", login="L", script="s", script_type="BOGUS")


def test_job_missing_host_rejected() -> None:
    with pytest.raises(ValidationError):
        JobSpec(login="L", script="s")  # type: ignore[call-arg]


def test_job_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        JobSpec(host="H", login="L", script="s", unexpected=True)  # type: ignore[call-arg]


def test_job_raw_escape_hatch() -> None:
    spec = JobSpec(host="H", login="L", script="s", raw={"OH_PLATFORM": "WIN"})
    assert spec.raw == {"OH_PLATFORM": "WIN"}


def test_job_registered_in_kind_registry() -> None:
    assert KIND_REGISTRY["Job"] is JobSpec
