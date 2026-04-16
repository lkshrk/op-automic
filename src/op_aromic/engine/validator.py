"""Cross-document manifest validator.

Runs after the loader has produced per-document Pydantic models. Checks
the rules that aren't expressible on a single doc in isolation: unique
identity tuples, that every ObjectRef resolves, that names/folders
follow Automic's conventions.
"""

from __future__ import annotations

import contextlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from op_aromic.engine.loader import LoadedManifest
from op_aromic.models.base import ObjectRef

# Automic object name alphabet per the AE docs: uppercase A-Z, digits, and
# the punctuation set `. _ # $ @`. Length cap 200.
_NAME_RE = re.compile(r"^[A-Z0-9._#$@]{1,200}$")


class Severity(StrEnum):
    """How strongly an issue is reported to the user."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    """A single validator finding attached to a source location."""

    severity: Severity
    message: str
    source_path: Path
    doc_index: int


@dataclass(frozen=True)
class ValidationReport:
    """Aggregate of issues across all loaded manifests."""

    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _identity(loaded: LoadedManifest) -> tuple[int | None, str, str, str]:
    md = loaded.manifest.metadata
    return (md.client, loaded.manifest.kind, md.folder, md.name)


def _walk_object_refs(value: Any) -> list[ObjectRef]:
    """Pull every ObjectRef out of a spec body, regardless of nesting."""
    found: list[ObjectRef] = []
    _walk_object_refs_into(value, found)
    return found


def _walk_object_refs_into(value: Any, acc: list[ObjectRef]) -> None:
    if isinstance(value, dict):
        if _looks_like_object_ref(value):
            # Swallow: a dict that happens to have {kind, name} keys but isn't
            # a well-formed ref is caught earlier by Pydantic; nothing to add.
            with contextlib.suppress(Exception):
                acc.append(ObjectRef.model_validate(value))
        for v in value.values():
            _walk_object_refs_into(v, acc)
    elif isinstance(value, list):
        for v in value:
            _walk_object_refs_into(v, acc)


def _looks_like_object_ref(d: dict[str, Any]) -> bool:
    keys = set(d.keys())
    return "kind" in keys and "name" in keys and keys.issubset({"kind", "name", "folder"})


def _validate_name(loaded: LoadedManifest) -> list[Issue]:
    name = loaded.manifest.metadata.name
    if _NAME_RE.match(name):
        return []
    if len(name) > 200:
        msg = f"metadata.name length {len(name)} exceeds Automic's 200 char max"
    else:
        msg = (
            f"metadata.name {name!r} has invalid characters "
            "(allowed: A-Z 0-9 . _ # $ @)"
        )
    return [
        Issue(
            severity=Severity.ERROR,
            message=msg,
            source_path=loaded.source_path,
            doc_index=loaded.doc_index,
        ),
    ]


def _validate_folder(loaded: LoadedManifest) -> list[Issue]:
    folder = loaded.manifest.metadata.folder
    if folder.startswith("/"):
        return []
    return [
        Issue(
            severity=Severity.ERROR,
            message=f"metadata.folder {folder!r} must start with '/'",
            source_path=loaded.source_path,
            doc_index=loaded.doc_index,
        ),
    ]


def _collect_duplicates(loaded: list[LoadedManifest]) -> list[Issue]:
    buckets: dict[tuple[int | None, str, str, str], list[LoadedManifest]] = defaultdict(list)
    for lm in loaded:
        buckets[_identity(lm)].append(lm)

    issues: list[Issue] = []
    for identity, members in buckets.items():
        if len(members) < 2:
            continue
        client, kind, folder, name = identity
        locations = ", ".join(f"{m.source_path}:doc{m.doc_index}" for m in members)
        for lm in members:
            issues.append(
                Issue(
                    severity=Severity.ERROR,
                    message=(
                        f"duplicate identity (client={client}, kind={kind}, "
                        f"folder={folder}, name={name}) at {locations}"
                    ),
                    source_path=lm.source_path,
                    doc_index=lm.doc_index,
                ),
            )
    return issues


def _collect_reference_issues(loaded: list[LoadedManifest]) -> list[Issue]:
    declared: set[tuple[str, str]] = {
        (lm.manifest.kind, lm.manifest.metadata.name) for lm in loaded
    }
    issues: list[Issue] = []
    for lm in loaded:
        for ref in _walk_object_refs(lm.manifest.spec):
            if (ref.kind, ref.name) not in declared:
                issues.append(
                    Issue(
                        severity=Severity.ERROR,
                        message=(
                            f"unresolved reference: kind={ref.kind} name={ref.name} "
                            "is not declared in the manifest set"
                        ),
                        source_path=lm.source_path,
                        doc_index=lm.doc_index,
                    ),
                )
    return issues


def validate_manifests(loaded: list[LoadedManifest]) -> ValidationReport:
    """Run every cross-document rule and return a single report."""
    errors: list[Issue] = []
    warnings: list[Issue] = []

    for lm in loaded:
        errors.extend(_validate_name(lm))
        errors.extend(_validate_folder(lm))

    errors.extend(_collect_duplicates(loaded))
    errors.extend(_collect_reference_issues(loaded))

    return ValidationReport(errors=errors, warnings=warnings)


__all__ = ["Issue", "Severity", "ValidationReport", "validate_manifests"]
