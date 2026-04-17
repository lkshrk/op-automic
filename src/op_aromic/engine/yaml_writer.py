"""Deterministic YAML writer used by ``aromic export``.

Writes a list of :class:`Manifest` objects to a single multi-doc YAML file
in a layout that is **byte-identical across calls** for the same input.
Determinism is the cornerstone: the round-trip quality bar
(``export → validate → plan`` ⇒ empty changeset) depends on it, and
readable git history depends on keys always showing up in the same order.

Ordering strategy:

- Top-level keys come from :class:`Manifest`'s ``model_fields`` (Pydantic v2
  preserves declaration order, so this is stable).
- ``spec`` is a free-form dict on the envelope, so we reach into
  :data:`KIND_REGISTRY` to fetch the kind's spec model and order its keys
  by that model's ``model_fields`` instead — producing the exact same layout
  we hand-write in the fixtures.
- Values that are nested Pydantic models (``ObjectRef``, ``WorkflowTask``,
  ``CalendarKeyword``, …) are ordered by their own ``model_fields`` too.

Style:

- Block style only (``default_flow_style=False``).
- No anchors or aliases (the writer never returns an anchored node, but we
  belt-and-braces in :func:`_represent_str` to force literal style on
  multi-line strings rather than ever allowing PyYAML to pick flow).
- UTF-8, LF newlines, a ``---`` separator between documents, and a single
  trailing newline.

The writer does **not** validate manifests; callers are expected to hand
it already-validated :class:`Manifest` instances.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from op_aromic.models.base import KIND_REGISTRY, Manifest

# Always write UTF-8 LF. Binary writes keep the exact byte stream stable
# across platforms so round-trip byte-equality holds on Windows too.
_ENCODING = "utf-8"


class _BlockStyleDumper(yaml.SafeDumper):
    """Custom dumper that disables flow style unconditionally and refuses aliases.

    ``ignore_aliases`` returning True prevents PyYAML from ever emitting an
    anchor/alias pair, even for repeated sub-structures. The output stays
    self-contained and diff-friendly.
    """

    def ignore_aliases(self, data: Any) -> bool:
        return True


def _represent_str(dumper: _BlockStyleDumper, data: str) -> yaml.Node:
    """Prefer plain style for strings unless the value contains a newline.

    Default PyYAML sometimes picks double-quoted for strings that look like
    numbers; that is fine and deterministic, so we let it decide. The one
    override we do want is: multi-line strings should use literal ``|``
    block style rather than awkward escaped ``\\n``.
    """
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_BlockStyleDumper.add_representer(str, _represent_str)


def _ordered_spec(kind: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Re-key ``spec`` by the per-kind spec model's declaration order.

    Any key not declared on the spec model is appended at the end in its
    existing iteration order — this is the ``spec.raw`` escape hatch's
    natural home. Dropping unknown keys would silently lose data; preserving
    them keeps the round-trip lossless for manifests that carry fields we
    have not formally modelled.
    """
    model = KIND_REGISTRY.get(kind)
    if model is None:
        return dict(spec)

    declared = list(model.model_fields.keys())
    ordered: dict[str, Any] = {}
    for name in declared:
        if name in spec:
            ordered[name] = _order_value(spec[name], _field_submodel(model, name))
    # Preserve unmodelled keys (e.g. carried via ``spec.raw`` flattening) at
    # the tail so nothing is dropped.
    for name, value in spec.items():
        if name not in ordered:
            ordered[name] = _order_value(value, None)
    return ordered


def _field_submodel(model: type[BaseModel], field_name: str) -> type[BaseModel] | None:
    """Return the nested BaseModel type for a field, if any.

    Used to recurse into list/dict values and keep ordering consistent for
    composite structures like ``WorkflowTask`` or ``CalendarKeyword``.
    """
    field_info = model.model_fields.get(field_name)
    if field_info is None:
        return None
    annotation = field_info.annotation
    return _extract_submodel(annotation)


def _extract_submodel(annotation: Any) -> type[BaseModel] | None:
    """Walk a type annotation and return the first BaseModel subclass found."""
    if annotation is None:
        return None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    # Handle list[X], dict[_, X], Optional[X], Annotated[X, ...] etc. by
    # scanning __args__ recursively; this is cheap and keeps the writer
    # insensitive to typing module changes.
    args = getattr(annotation, "__args__", ())
    for arg in args:
        found = _extract_submodel(arg)
        if found is not None:
            return found
    return None


def _order_value(value: Any, sub_model: type[BaseModel] | None) -> Any:
    """Recursively order mappings/lists so output is stable.

    ``sub_model`` is the nested Pydantic model associated with this slot
    (e.g. ``WorkflowTask`` for ``spec.tasks[*]``). When set, mapping keys
    are re-ordered to match that model's field declaration order;
    otherwise we leave the existing iteration order alone (insertion
    order in Python 3.7+ is stable, and the caller always builds dicts in
    a deterministic way).
    """
    if isinstance(value, dict):
        if sub_model is None:
            return {k: _order_value(v, None) for k, v in value.items()}
        declared = list(sub_model.model_fields.keys())
        ordered: dict[str, Any] = {}
        for name in declared:
            if name in value:
                nested = _field_submodel(sub_model, name)
                ordered[name] = _order_value(value[name], nested)
        for name, nested_value in value.items():
            if name not in ordered:
                ordered[name] = _order_value(nested_value, None)
        return ordered
    if isinstance(value, list):
        return [_order_value(v, sub_model) for v in value]
    return value


def _manifest_to_ordered_dict(manifest: Manifest) -> dict[str, Any]:
    """Dump a manifest to a plain dict with top-level keys in declared order.

    We ``by_alias=True`` so ``apiVersion`` (rather than the Python field
    name ``api_version``) reaches the wire in the canonical Kubernetes form.
    """
    raw = manifest.model_dump(by_alias=True, exclude_none=False)
    ordered: dict[str, Any] = {}
    for name in Manifest.model_fields:
        alias = Manifest.model_fields[name].alias or name
        if alias in raw:
            ordered[alias] = raw[alias]
        elif name in raw:
            ordered[name] = raw[name]

    # Metadata: preserve its model's field order and drop keys whose value is
    # None or empty-dict so we do not emit ``client: null`` when the user
    # did not set it.
    metadata_key = "metadata"
    if metadata_key in ordered and isinstance(ordered[metadata_key], dict):
        ordered[metadata_key] = _reorder_by_model(
            ordered[metadata_key],
            manifest.metadata.__class__,
            drop_empty=True,
        )

    # Spec gets per-kind ordering.
    if "spec" in ordered and isinstance(ordered["spec"], dict):
        ordered["spec"] = _ordered_spec(manifest.kind, ordered["spec"])

    # ``status`` is omitted entirely when the manifest has none (user-authored
    # files should not carry a null status). When present, preserve the
    # Status model's field order and drop any individually-null fields so
    # partial server responses render cleanly.
    status_key = "status"
    if status_key in ordered:
        status_value = ordered[status_key]
        if status_value is None:
            del ordered[status_key]
        elif isinstance(status_value, dict) and manifest.status is not None:
            ordered[status_key] = _reorder_by_model(
                status_value,
                manifest.status.__class__,
                drop_empty=True,
            )
            if not ordered[status_key]:
                del ordered[status_key]

    return ordered


def _reorder_by_model(
    value: dict[str, Any],
    model: type[BaseModel],
    *,
    drop_empty: bool,
) -> dict[str, Any]:
    """Re-key a dict by a Pydantic model's declaration order.

    ``drop_empty=True`` skips keys whose value is ``None`` or an empty
    dict; used on the metadata block so optional fields do not leak null
    defaults into the file.

    Alias-aware: dicts produced by ``model_dump(by_alias=True)`` use the
    alias key (e.g. ``automicVersion``), so we prefer the alias when the
    field declares one and fall back to the Python field name.
    """
    ordered: dict[str, Any] = {}
    for name, field_info in model.model_fields.items():
        key = field_info.alias or name
        present_key = key if key in value else (name if name in value else None)
        if present_key is None:
            continue
        v = value[present_key]
        if drop_empty and (v is None or v == {}):
            continue
        nested = _field_submodel(model, name)
        ordered[present_key] = _order_value(v, nested)
    for name, v in value.items():
        if name not in ordered and not (drop_empty and (v is None or v == {})):
            ordered[name] = _order_value(v, None)
    return ordered


def write_manifest_yaml(manifests: list[Manifest], path: Path) -> None:
    """Write ``manifests`` to ``path`` as a multi-doc YAML file.

    Alias kept for the exporter; :func:`write_manifests_to_file` is the
    same thing with the argument order flipped to match the convention
    used elsewhere (``path`` first).
    """
    write_manifests_to_file(path, manifests)


def write_manifests_to_file(path: Path, manifests: list[Manifest]) -> None:
    """Serialise ``manifests`` to ``path`` deterministically.

    Empty manifest lists write an empty file (no ``---``, just a trailing
    newline) so diffs stay readable. Parent directories must exist; the
    exporter is responsible for creating them.
    """
    body = render_manifests(manifests)
    path.write_bytes(body.encode(_ENCODING))


def render_manifests(manifests: list[Manifest]) -> str:
    """Pure function: manifests → YAML text. Easier to unit-test than the
    file-writing variant and lets the exporter preflight a dry run.
    """
    if not manifests:
        return ""
    ordered_docs = [_manifest_to_ordered_dict(m) for m in manifests]
    text = yaml.dump_all(
        ordered_docs,
        Dumper=_BlockStyleDumper,
        default_flow_style=False,
        sort_keys=False,
        explicit_start=True,
        allow_unicode=True,
        width=1_000_000,  # avoid line-wrapping; keeps diffs stable
    )
    if not text.endswith("\n"):
        text += "\n"
    return text


__all__ = [
    "render_manifests",
    "write_manifest_yaml",
    "write_manifests_to_file",
]
