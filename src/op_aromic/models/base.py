"""Shared Pydantic models and the per-kind spec registry.

The Manifest model is the Kubernetes-style envelope shared by every kind;
per-kind `spec` shapes live alongside in their own modules and register
themselves into `KIND_REGISTRY` at import time.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Populated by per-kind modules when they're imported. Maps the YAML `kind`
# value (e.g. "Workflow") to the Pydantic model class used for its `spec`.
KIND_REGISTRY: dict[str, type[BaseModel]] = {}


class Metadata(BaseModel):
    """Shared metadata block. `name`+`folder` (scoped by client) is identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Length and character rules live in the engine validator so it can
    # report all offenders at once with file:line context; the envelope
    # only enforces non-empty.
    name: Annotated[str, Field(min_length=1)]
    folder: Annotated[str, Field(min_length=1)]
    client: int | None = None
    annotations: dict[str, str] = Field(default_factory=dict)
    # Content-hash revision over the canonical spec. Populated by the
    # exporter and recomputed on load to detect tampering. `sha256:<hex>`.
    # Optional on input: missing revision is computed at load time rather
    # than rejected so hand-authored manifests stay ergonomic.
    revision: str | None = None


class Status(BaseModel):
    """Server-side read-only fields surfaced for humans and the ledger.

    The differ ignores this block — it is presentation metadata, never
    authoritative source. The exporter populates it from Automic's
    response; the applier overwrites it after a successful write.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    automic_version: int | None = Field(default=None, alias="automicVersion")
    last_modified: str | None = Field(default=None, alias="lastModified")
    last_modified_by: str | None = Field(default=None, alias="lastModifiedBy")


class ObjectRef(BaseModel):
    """A structured reference to another Automic object declared in the manifests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1)]
    folder: str | None = None


class Manifest(BaseModel):
    """Kubernetes-style multi-doc YAML envelope."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    api_version: Annotated[str, Field(alias="apiVersion")]
    kind: Annotated[str, Field(min_length=1)]
    metadata: Metadata
    # `spec` is intentionally typed as dict here: the loader re-validates the
    # body against the per-kind model from KIND_REGISTRY once it knows the
    # concrete kind. Using `Any` would hide the fact that we expect a mapping.
    spec: dict[str, Any] = Field(default_factory=dict)
    # Server-observed read-only fields; omitted from user-authored manifests
    # and excluded from the diff. Populated by the exporter/applier only.
    status: Status | None = None

    SUPPORTED_API_VERSION_PREFIX: ClassVar[str] = "aromic.io/"

    @field_validator("api_version")
    @classmethod
    def _validate_api_version(cls, v: str) -> str:
        if not v.startswith(cls.SUPPORTED_API_VERSION_PREFIX):
            raise ValueError(
                f"apiVersion must start with '{cls.SUPPORTED_API_VERSION_PREFIX}', got {v!r}",
            )
        return v


__all__ = ["KIND_REGISTRY", "Manifest", "Metadata", "ObjectRef", "Status"]
