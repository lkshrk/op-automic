"""End-to-end integration tests for revision + status + ledger wiring."""

from __future__ import annotations

from pathlib import Path

from op_aromic.engine.loader import load_manifests
from op_aromic.engine.revision import compute_revision
from op_aromic.engine.yaml_writer import write_manifests_to_file
from op_aromic.models.base import Manifest, Status


def _build(name: str = "FOO", title: str = "T", revision: str | None = None) -> Manifest:
    metadata = {"name": name, "folder": "/X"}
    if revision is not None:
        metadata["revision"] = revision
    return Manifest.model_validate(
        {
            "apiVersion": "aromic.io/v1",
            "kind": "Workflow",
            "metadata": metadata,
            "spec": {"title": title, "tasks": []},
        },
    )


def test_load_stamps_revision_when_missing(tmp_path: Path) -> None:
    target = tmp_path / "wf.yaml"
    target.write_text(
        "apiVersion: aromic.io/v1\n"
        "kind: Workflow\n"
        "metadata:\n"
        "  name: FOO\n"
        "  folder: /X\n"
        "spec:\n"
        "  title: T\n"
        "  tasks: []\n",
    )
    loaded = load_manifests(target)
    assert len(loaded) == 1
    lm = loaded[0]
    # No declared revision → no mismatch warning, revision stamped freshly.
    assert lm.declared_revision is None
    assert lm.revision_mismatch is False
    assert lm.manifest.metadata.revision is not None
    assert lm.manifest.metadata.revision.startswith("sha256:")


def test_load_detects_mismatch(tmp_path: Path) -> None:
    target = tmp_path / "wf.yaml"
    bogus = "sha256:" + "0" * 64
    target.write_text(
        "apiVersion: aromic.io/v1\n"
        "kind: Workflow\n"
        "metadata:\n"
        "  name: FOO\n"
        "  folder: /X\n"
        f"  revision: {bogus}\n"
        "spec:\n"
        "  title: T\n"
        "  tasks: []\n",
    )
    loaded = load_manifests(target)
    assert len(loaded) == 1
    lm = loaded[0]
    assert lm.revision_mismatch is True
    assert lm.declared_revision == bogus
    # Loader re-stamps with the correct revision so downstream code is uniform.
    assert lm.manifest.metadata.revision != bogus


def test_load_accepts_matching_revision(tmp_path: Path) -> None:
    m = _build()
    correct_rev = compute_revision(m)
    target = tmp_path / "wf.yaml"
    target.write_text(
        "apiVersion: aromic.io/v1\n"
        "kind: Workflow\n"
        "metadata:\n"
        "  name: FOO\n"
        "  folder: /X\n"
        f"  revision: {correct_rev}\n"
        "spec:\n"
        "  title: T\n"
        "  tasks: []\n",
    )
    loaded = load_manifests(target)
    lm = loaded[0]
    assert lm.revision_mismatch is False
    assert lm.manifest.metadata.revision == correct_rev


def test_yaml_writer_roundtrips_status(tmp_path: Path) -> None:
    m = _build()
    stamped_status = Status(
        automic_version=42,
        last_modified="2026-04-17T12:00Z",
        last_modified_by="ADMIN",
    )
    stamped = m.model_copy(update={"status": stamped_status})

    target = tmp_path / "wf.yaml"
    write_manifests_to_file(target, [stamped])

    text = target.read_text()
    assert "status:" in text
    assert "automicVersion: 42" in text
    assert "lastModifiedBy: ADMIN" in text

    # Round-trip: load + ensure status preserved.
    loaded = load_manifests(target)
    assert loaded[0].manifest.status is not None
    assert loaded[0].manifest.status.automic_version == 42
    assert loaded[0].manifest.status.last_modified_by == "ADMIN"


def test_yaml_writer_omits_null_status(tmp_path: Path) -> None:
    m = _build()
    target = tmp_path / "wf.yaml"
    write_manifests_to_file(target, [m])
    # A user-authored manifest must not emit a null status block.
    assert "status:" not in target.read_text()


def test_exporter_stamps_revision_and_status() -> None:
    # Exporter uses a flat (legacy) Automic payload; assert the built
    # manifest carries both a computed revision and the server-side
    # status fields we routed into Status.
    from op_aromic.engine.exporter import _payload_to_manifest

    payload = {
        "Name": "FOO",
        "Folder": "/X",
        "Type": "JOBP",
        "Title": "T",
        "Tasks": [],
        "VersionNumber": "7",
        "LastModified": "2026-04-17T10:00Z",
        "LastModifiedBy": "CLAUDE",
    }
    manifest = _payload_to_manifest("Workflow", payload)
    assert manifest.metadata.revision is not None
    assert manifest.metadata.revision.startswith("sha256:")
    assert manifest.status is not None
    assert manifest.status.automic_version == 7
    assert manifest.status.last_modified_by == "CLAUDE"
    # Revision must be deterministic on the spec alone.
    from op_aromic.engine.revision import compute_revision
    assert compute_revision(manifest) == manifest.metadata.revision
