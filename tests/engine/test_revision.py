"""Tests for canonical revision computation and validation."""

from __future__ import annotations

from op_aromic.engine.revision import (
    compute_revision,
    compute_revision_from_canonical,
    is_revision,
)
from op_aromic.models.base import Manifest


def _manifest(name: str = "FOO", title: str | None = "T") -> Manifest:
    return Manifest.model_validate(
        {
            "apiVersion": "aromic.io/v1",
            "kind": "Workflow",
            "metadata": {"name": name, "folder": "/X"},
            "spec": {"title": title, "tasks": []},
        },
    )


def test_revision_format() -> None:
    rev = compute_revision(_manifest())
    assert rev.startswith("sha256:")
    assert is_revision(rev)


def test_is_revision_rejects_malformed() -> None:
    assert not is_revision(None)  # type: ignore[arg-type]
    assert not is_revision("")
    assert not is_revision("sha256:abc")
    assert not is_revision("md5:" + "a" * 64)
    assert not is_revision("sha256:" + "Z" * 64)  # non-hex


def test_revision_stable_across_calls() -> None:
    m = _manifest()
    assert compute_revision(m) == compute_revision(m)


def test_revision_changes_on_spec_change() -> None:
    a = compute_revision(_manifest(title="A"))
    b = compute_revision(_manifest(title="B"))
    assert a != b


def test_revision_ignores_identity_fields() -> None:
    # Renaming the object must not change its content revision; the
    # ledger key already carries identity.
    a = compute_revision(_manifest(name="FOO"))
    b = compute_revision(_manifest(name="BAR"))
    assert a == b


def test_canonical_helper_matches_manifest_helper() -> None:
    from op_aromic.engine.normalizer import to_canonical_from_manifest

    m = _manifest()
    canonical = to_canonical_from_manifest(m)
    assert compute_revision_from_canonical(canonical) == compute_revision(m)
