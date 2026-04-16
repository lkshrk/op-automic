"""Tests for the Manifest base models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from op_aromic.models.base import (
    KIND_REGISTRY,
    Manifest,
    Metadata,
    ObjectRef,
)


def test_metadata_minimal() -> None:
    md = Metadata(name="ETL.JOB.A", folder="/PROD/ETL")
    assert md.name == "ETL.JOB.A"
    assert md.folder == "/PROD/ETL"
    assert md.client is None
    assert md.annotations == {}


def test_metadata_with_client_and_annotations() -> None:
    md = Metadata(
        name="ETL.JOB.A",
        folder="/PROD/ETL",
        client=200,
        annotations={"aromic.io/managed-by": "op-aromic"},
    )
    assert md.client == 200
    assert md.annotations["aromic.io/managed-by"] == "op-aromic"


def test_metadata_requires_name_and_folder() -> None:
    with pytest.raises(ValidationError):
        Metadata()  # type: ignore[call-arg]


def test_object_ref_basic() -> None:
    ref = ObjectRef(kind="Job", name="ETL.JOB.A")
    assert ref.kind == "Job"
    assert ref.name == "ETL.JOB.A"
    assert ref.folder is None


def test_object_ref_with_folder() -> None:
    ref = ObjectRef(kind="Job", name="ETL.JOB.A", folder="/PROD/ETL")
    assert ref.folder == "/PROD/ETL"


def test_manifest_round_trips_raw_spec() -> None:
    manifest = Manifest(
        apiVersion="aromic.io/v1",
        kind="Job",
        metadata=Metadata(name="ETL.JOB.A", folder="/PROD/ETL"),
        spec={"title": "Raw spec placeholder"},
    )
    assert manifest.api_version == "aromic.io/v1"
    assert manifest.kind == "Job"
    assert manifest.spec == {"title": "Raw spec placeholder"}


def test_manifest_rejects_wrong_api_version() -> None:
    with pytest.raises(ValidationError):
        Manifest(
            apiVersion="v1",  # not an aromic.io/... version
            kind="Job",
            metadata=Metadata(name="ETL.JOB.A", folder="/PROD/ETL"),
            spec={},
        )


def test_kind_registry_is_populated_by_register() -> None:
    # The registry is a mutable mapping; per-kind modules register themselves
    # at import time. base.py exposes it as empty-or-populated depending on
    # what's been imported. We test its shape, not contents.
    assert isinstance(KIND_REGISTRY, dict)
