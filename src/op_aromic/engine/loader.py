"""Load and parse YAML manifest files.

The loader is intentionally strict: any YAML parse error, unknown kind, or
per-kind Pydantic validation failure is raised as a `ManifestError` with
the offending file path (and, where available, a line number) attached.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from op_aromic.engine.errors import ManifestError
from op_aromic.models.base import KIND_REGISTRY, Manifest

_YAML_SUFFIXES = frozenset({".yaml", ".yml"})


class _LineSafeLoader(yaml.SafeLoader):
    """Annotates mapping nodes with `__line__` for downstream error reporting."""


def _construct_mapping_with_line(
    loader: _LineSafeLoader, node: yaml.MappingNode
) -> dict[str, Any]:
    raw = loader.construct_mapping(node, deep=True)
    # Cast all keys to str so downstream code (and type checkers) can rely
    # on it; YAML allows non-string keys but manifests never use them.
    mapping: dict[str, Any] = {str(k): v for k, v in raw.items()}
    # PyYAML's start_mark.line is 0-based; humans count from 1.
    mapping["__line__"] = node.start_mark.line + 1
    return mapping


_LineSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_with_line,
)


@dataclass(frozen=True)
class LoadedManifest:
    """A single successfully-loaded manifest, carrying its source context."""

    source_path: Path
    doc_index: int
    manifest: Manifest


def _iter_manifest_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in _YAML_SUFFIXES)


def _strip_line_markers(value: Any) -> Any:
    """Recursively drop `__line__` sentinels before handing data to Pydantic."""
    if isinstance(value, dict):
        return {k: _strip_line_markers(v) for k, v in value.items() if k != "__line__"}
    if isinstance(value, list):
        return [_strip_line_markers(v) for v in value]
    return value


def _parse_docs(path: Path) -> list[dict[str, Any]]:
    try:
        raw_text = path.read_text()
    except OSError as exc:  # pragma: no cover - filesystem errors are rare in tests
        raise ManifestError(f"cannot read file: {exc}", source_path=path) from exc

    try:
        docs = list(yaml.load_all(raw_text, Loader=_LineSafeLoader))
    except yaml.YAMLError as exc:
        line = None
        if hasattr(exc, "problem_mark") and exc.problem_mark is not None:
            line = exc.problem_mark.line + 1
        raise ManifestError(f"YAML parse error: {exc}", source_path=path, line=line) from exc

    out: list[dict[str, Any]] = []
    for doc in docs:
        if doc is None:
            continue
        if not isinstance(doc, dict):
            raise ManifestError(
                "expected a mapping at document root",
                source_path=path,
            )
        out.append(doc)
    return out


def _build_manifest(doc: dict[str, Any], *, path: Path, doc_index: int) -> Manifest:
    doc_line = doc.get("__line__")
    clean = _strip_line_markers(doc)

    kind = clean.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ManifestError(
            "manifest is missing `kind`",
            source_path=path,
            line=doc_line if isinstance(doc_line, int) else None,
        )

    spec_model = KIND_REGISTRY.get(kind)
    if spec_model is None:
        known = ", ".join(sorted(KIND_REGISTRY.keys())) or "<none>"
        raise ManifestError(
            f"unknown kind {kind!r} in doc {doc_index} (known: {known})",
            source_path=path,
            line=doc_line if isinstance(doc_line, int) else None,
        )

    try:
        manifest = Manifest.model_validate(clean)
    except PydanticValidationError as exc:
        raise ManifestError(
            f"invalid manifest envelope: {exc}",
            source_path=path,
            line=doc_line if isinstance(doc_line, int) else None,
        ) from exc

    try:
        spec_model.model_validate(manifest.spec)
    except PydanticValidationError as exc:
        raise ManifestError(
            f"invalid {kind} spec: {exc}",
            source_path=path,
            line=doc_line if isinstance(doc_line, int) else None,
        ) from exc

    return manifest


def load_manifests(path: Path | str) -> list[LoadedManifest]:
    """Walk `path` (file or directory) and parse every `.yaml`/`.yml` document.

    Raises ManifestError on the first failure, with file (and line if known)
    context attached.
    """
    root = Path(path)
    if not root.exists():
        raise ManifestError(f"path does not exist: {root}", source_path=root)

    results: list[LoadedManifest] = []
    for file_path in _iter_manifest_files(root):
        docs = _parse_docs(file_path)
        for doc_index, doc in enumerate(docs):
            manifest = _build_manifest(doc, path=file_path, doc_index=doc_index)
            results.append(
                LoadedManifest(
                    source_path=file_path,
                    doc_index=doc_index,
                    manifest=manifest,
                ),
            )
    return results


__all__ = ["LoadedManifest", "load_manifests"]
