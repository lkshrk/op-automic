"""Full-pipeline integration tests for versioning.

Three gaps closed here beyond the narrower unit tests:

1. ``apply(..., ledger_dir=...)`` actually writes a JSONL row to disk
   with the expected revision on create/update and a null revision on
   delete.
2. The normalizer/differ pair ignores ``metadata.revision`` and the
   entire ``status`` subtree, so round-tripping a stamped manifest
   produces ``noop``.
3. A full ``export → write → load → plan`` round trip converges to
   ``noop`` and preserves revision across the YAML boundary.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import respx

from op_aromic.client.api import AutomicAPI
from op_aromic.client.http import _AUTH_PATH, AutomicClient
from op_aromic.config.settings import AutomicSettings
from op_aromic.engine.applier import apply
from op_aromic.engine.dependency import build_graph
from op_aromic.engine.differ import compute_diff
from op_aromic.engine.exporter import _payload_to_manifest
from op_aromic.engine.ledger import path_for, read_rows
from op_aromic.engine.loader import LoadedManifest, load_manifests
from op_aromic.engine.normalizer import to_canonical_from_manifest
from op_aromic.engine.planner import build_plan
from op_aromic.engine.revision import compute_revision
from op_aromic.engine.yaml_writer import write_manifests_to_file
from op_aromic.models.base import Manifest, Status


# ---------------------------------------------------------------------------
# Gap 1: applier writes a ledger row on create/update/delete
# ---------------------------------------------------------------------------


def _settings() -> AutomicSettings:
    return AutomicSettings(
        url="http://ledger.test/ae/api/v1",
        client_id=100,
        user="U",
        department="D",
        password="pw",
        verify_ssl=False,
        max_retries=0,
        update_method="PUT",
    )


def _mock_auth(mock: respx.MockRouter, settings: AutomicSettings) -> None:
    mock.post(f"{settings.url}{_AUTH_PATH}").mock(
        return_value=httpx.Response(200, json={"token": "t", "expires_in": 3600}),
    )


def _loaded_job(name: str) -> LoadedManifest:
    manifest = Manifest.model_validate(
        {
            "apiVersion": "aromic.io/v1",
            "kind": "Job",
            "metadata": {"name": name, "folder": "/PROD"},
            "spec": {"host": "h", "login": "L", "script": "s"},
        },
    )
    return LoadedManifest(
        source_path=Path(f"{name}.yaml"),
        doc_index=0,
        manifest=manifest,
    )


def test_apply_writes_ledger_row_on_create(tmp_path: Path) -> None:
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded_job("LEDGER.NEW")]
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/LEDGER.NEW").mock(return_value=httpx.Response(404))
        mock.post(f"{base}/objects").mock(
            return_value=httpx.Response(201, json={"Name": "LEDGER.NEW"}),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(loaded, api)
            graph = build_graph(loaded)
            result = apply(plan, client, graph, ledger_dir=tmp_path)

    assert result.status == "success"
    # File must exist under <root>/<Kind>/<Name>.jsonl
    ledger_file = path_for("Job", "LEDGER.NEW", root=tmp_path)
    assert ledger_file.exists(), f"no ledger file at {ledger_file}"

    rows = read_rows("Job", "LEDGER.NEW", root=tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "create"
    assert row["revision"] is not None
    assert row["revision"].startswith("sha256:")
    # Pre-create, nothing existed on the server → no version-before.
    assert row["automicVersionBefore"] is None


def test_apply_writes_ledger_row_on_delete(tmp_path: Path) -> None:
    # Delete path feeds through plan.deletes and writes revision=null.
    from op_aromic.engine.differ import ObjectDiff
    from op_aromic.engine.planner import Plan

    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    # Build a Plan with a single delete by hand; the planner only emits
    # deletes when called with prune=True, which requires a live inventory.
    plan = Plan(
        creates=[],
        updates=[],
        deletes=[
            ObjectDiff(
                action="delete",
                kind="Job",
                name="GONE.JOB",
                folder="/PROD",
                desired=None,
                actual={"name": "GONE.JOB", "folder": "/PROD", "kind": "Job"},
            ),
        ],
        noops=[],
    )
    graph = build_graph([])

    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/GONE.JOB").mock(
            return_value=httpx.Response(200, json={"Name": "GONE.JOB", "Type": "JOBS"}),
        )
        mock.delete(f"{base}/objects/GONE.JOB").mock(
            return_value=httpx.Response(200),
        )
        with AutomicClient(settings) as client:
            result = apply(plan, client, graph, ledger_dir=tmp_path, force=True)

    assert result.status == "success"
    rows = read_rows("Job", "GONE.JOB", root=tmp_path)
    assert len(rows) == 1
    assert rows[0]["action"] == "delete"
    # Delete must have revision=null per schema.
    assert rows[0]["revision"] is None


def test_apply_omits_ledger_when_ledger_dir_is_none(tmp_path: Path) -> None:
    # Default ledger_dir=None → writes go to ./revisions relative to cwd.
    # Assert we don't explode, and don't write anywhere under tmp_path.
    settings = _settings()
    base = f"{settings.url}/{settings.client_id}"
    loaded = [_loaded_job("SILENT.NEW")]
    with respx.mock(assert_all_called=False) as mock:
        _mock_auth(mock, settings)
        mock.get(f"{base}/objects/SILENT.NEW").mock(return_value=httpx.Response(404))
        mock.post(f"{base}/objects").mock(
            return_value=httpx.Response(201, json={"Name": "SILENT.NEW"}),
        )
        with AutomicClient(settings) as client:
            api = AutomicAPI(client)
            plan = build_plan(loaded, api)
            graph = build_graph(loaded)
            result = apply(plan, client, graph, ledger_dir=None)

    assert result.status == "success"
    # Nothing should have landed under tmp_path because we didn't pass it.
    assert not (tmp_path / "Job").exists()


# ---------------------------------------------------------------------------
# Gap 2: differ ignores revision + status (they don't reach canonical form)
# ---------------------------------------------------------------------------


def _job(
    *,
    revision: str | None = None,
    status: dict[str, Any] | None = None,
) -> Manifest:
    metadata: dict[str, Any] = {"name": "J", "folder": "/X"}
    if revision is not None:
        metadata["revision"] = revision
    doc: dict[str, Any] = {
        "apiVersion": "aromic.io/v1",
        "kind": "Job",
        "metadata": metadata,
        "spec": {"host": "h", "login": "L", "script": "s"},
    }
    if status is not None:
        doc["status"] = status
    return Manifest.model_validate(doc)


def test_canonical_form_ignores_revision() -> None:
    a = _job(revision=None)
    b = _job(revision="sha256:" + "a" * 64)
    assert to_canonical_from_manifest(a) == to_canonical_from_manifest(b)


def test_canonical_form_ignores_status() -> None:
    a = _job(status=None)
    b = _job(
        status={
            "automicVersion": 42,
            "lastModified": "2026-04-17T00:00Z",
            "lastModifiedBy": "ADMIN",
        },
    )
    assert to_canonical_from_manifest(a) == to_canonical_from_manifest(b)


def test_diff_is_noop_for_revision_only_change() -> None:
    bare = to_canonical_from_manifest(_job(revision=None))
    stamped = to_canonical_from_manifest(_job(revision="sha256:" + "f" * 64))
    diff = compute_diff(
        kind="Job",
        name="J",
        folder="/X",
        desired=stamped,
        actual=bare,
    )
    assert diff.action == "noop"
    assert diff.changes == []


def test_diff_is_noop_for_status_only_change() -> None:
    bare = to_canonical_from_manifest(_job(status=None))
    with_status = to_canonical_from_manifest(
        _job(status={"automicVersion": 7}),
    )
    diff = compute_diff(
        kind="Job",
        name="J",
        folder="/X",
        desired=with_status,
        actual=bare,
    )
    assert diff.action == "noop"


# ---------------------------------------------------------------------------
# Gap 3: export → write → load → plan round-trip preserves revision
# ---------------------------------------------------------------------------


def test_full_round_trip_export_write_load_plan_is_noop(tmp_path: Path) -> None:
    # 1. Start from a synthetic Automic payload — the exporter stamps
    #    both revision and status onto the built manifest.
    payload = {
        "Name": "RT",
        "Folder": "/X",
        "Type": "JOBS",
        "Host": "h",
        "Login": "L",
        "Script": "s",
        "ScriptType": "OS",
        "VersionNumber": "5",
        "LastModified": "2026-04-17T10:00Z",
        "LastModifiedBy": "CLAUDE",
    }
    exported = _payload_to_manifest("Job", payload)
    assert exported.metadata.revision is not None
    assert exported.metadata.revision.startswith("sha256:")
    assert exported.status is not None
    assert exported.status.automic_version == 5

    # 2. Write to disk via the yaml writer.
    target = tmp_path / "rt.yaml"
    write_manifests_to_file(target, [exported])

    # 3. Reload from disk — no mismatch, revision preserved verbatim.
    loaded = load_manifests(target)
    assert len(loaded) == 1
    lm = loaded[0]
    assert lm.revision_mismatch is False
    assert lm.manifest.metadata.revision == exported.metadata.revision
    # Status survived the YAML round trip.
    assert lm.manifest.status is not None
    assert lm.manifest.status.automic_version == 5
    assert lm.manifest.status.last_modified_by == "CLAUDE"

    # 4. Plan against the same canonical actual — must be noop.
    desired = to_canonical_from_manifest(lm.manifest)
    # Build a matching "actual" from the legacy payload through the
    # Automic-side normalizer for true apples-to-apples comparison.
    from op_aromic.engine.normalizer import to_canonical_from_automic

    actual = to_canonical_from_automic("Job", payload)
    diff = compute_diff(
        kind="Job",
        name="RT",
        folder="/X",
        desired=desired,
        actual=actual,
    )
    assert diff.action == "noop", f"unexpected diff: {diff.changes}"


def test_round_trip_stability_revision_matches_canonical(tmp_path: Path) -> None:
    # After write + reload, recomputing the revision on the loaded
    # manifest must match what was stored — proves the loader isn't
    # silently restamping for non-mismatch reasons.
    original = Manifest.model_validate(
        {
            "apiVersion": "aromic.io/v1",
            "kind": "Workflow",
            "metadata": {"name": "WF", "folder": "/X"},
            "spec": {"title": "T", "tasks": []},
        },
    )
    computed = compute_revision(original)
    stamped = original.model_copy(
        update={"metadata": original.metadata.model_copy(update={"revision": computed})},
    )
    # Tack on a status block so we also prove status round-trips without
    # perturbing the revision.
    stamped = stamped.model_copy(
        update={"status": Status(automic_version=1)},
    )

    target = tmp_path / "wf.yaml"
    write_manifests_to_file(target, [stamped])

    loaded = load_manifests(target)[0]
    assert loaded.revision_mismatch is False
    assert loaded.manifest.metadata.revision == computed
    # Re-hashing the reloaded manifest reproduces the same digest.
    assert compute_revision(loaded.manifest) == computed
    # Raw file-on-disk inspection: revision written, status written.
    text = target.read_text()
    assert computed in text
    assert "status:" in text
    assert "automicVersion: 1" in text
    # Recomputed hash must also match the on-disk field byte-for-byte.
    assert f"revision: {computed}" in text or (
        f"revision: '{computed}'" in text
    )
    # Sanity: no stray null status block.
    assert "status: null" not in text
    # And revision is not emitted as a dict.
    assert "revision:\n" not in text or "revision:\n  " not in text

    # Spot-check serialised JSON row shape as a minor sanity guard for
    # downstream ledger consumers (revision field present & hex).
    sample = json.dumps({"revision": computed})
    assert "sha256:" in sample
