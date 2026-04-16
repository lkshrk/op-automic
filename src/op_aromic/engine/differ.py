"""Diff a desired-canonical and actual-canonical object.

Both sides go through the normalizer first — this module never sees raw
Automic JSON or raw manifests. DeepDiff is used in ``ignore_order=True``
mode to keep the engine insensitive to list reorderings; any ordering
that is semantic must be normalised to a sorted canonical form upstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from deepdiff import DeepDiff

Action = Literal["create", "update", "delete", "noop"]


@dataclass(frozen=True)
class FieldChange:
    """A single path-level change inside an ObjectDiff."""

    path: str
    before: Any
    after: Any
    kind: Literal["added", "removed", "changed"]


@dataclass(frozen=True)
class ObjectDiff:
    """The unit of a Plan: the difference between desired and actual for one object.

    ``identity`` is the stable (kind, name, folder) tuple used to sort and
    display diffs. ``changes`` is empty for ``noop`` and ``delete``, and
    contains one entry per differing path for ``create``/``update``.
    """

    action: Action
    kind: str
    name: str
    folder: str
    desired: dict[str, Any] | None
    actual: dict[str, Any] | None
    changes: list[FieldChange] = field(default_factory=list)


def _as_mapping(value: Any) -> dict[str, Any]:
    # DeepDiff uses SetOrdered at verbose_level=0 (just the paths) and a
    # mapping at verbose_level>=2 (paths plus old/new values). Callers
    # always build the diff with verbose_level=2; this helper silently
    # adapts if the bucket is empty or missing entirely.
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    return {path: None for path in value}


def _field_changes_from_deepdiff(diff: DeepDiff) -> list[FieldChange]:
    """Flatten DeepDiff's verbose output into a simple FieldChange list."""
    changes: list[FieldChange] = []

    for path, new_val in _as_mapping(diff.get("dictionary_item_added")).items():
        changes.append(
            FieldChange(path=_prettify_path(path), before=None, after=new_val, kind="added"),
        )
    for path, old_val in _as_mapping(diff.get("dictionary_item_removed")).items():
        changes.append(
            FieldChange(path=_prettify_path(path), before=old_val, after=None, kind="removed"),
        )
    for path, delta in _as_mapping(diff.get("values_changed")).items():
        before = delta.get("old_value") if isinstance(delta, dict) else None
        after = delta.get("new_value") if isinstance(delta, dict) else None
        changes.append(
            FieldChange(
                path=_prettify_path(path),
                before=before,
                after=after,
                kind="changed",
            ),
        )
    for path, delta in _as_mapping(diff.get("type_changes")).items():
        before = delta.get("old_value") if isinstance(delta, dict) else None
        after = delta.get("new_value") if isinstance(delta, dict) else None
        changes.append(
            FieldChange(
                path=_prettify_path(path),
                before=before,
                after=after,
                kind="changed",
            ),
        )
    # Iterable changes (added/removed items) are not surfaced as individual
    # paths here — they imply a list changed; the value_changed/dict_items
    # paths cover the common cases. ignore_order=True collapses permutations.
    return sorted(changes, key=lambda c: c.path)


def _prettify_path(raw: str) -> str:
    """Turn DeepDiff's ``root['a']['b'][0]`` into ``a.b[0]``."""
    # DeepDiff emits paths like "root['title']" or "root['tasks'][0]['name']".
    pretty = raw
    if pretty.startswith("root"):
        pretty = pretty[4:]
    # strip square quotes while preserving indexes
    out: list[str] = []
    token = ""
    in_key = False
    for ch in pretty:
        if ch == "[":
            if token:
                out.append(token)
                token = ""
            in_key = True
            continue
        if ch == "]":
            if in_key and token.startswith("'") and token.endswith("'"):
                out.append(token[1:-1])
            elif in_key:
                out.append(f"[{token}]")
            token = ""
            in_key = False
            continue
        token += ch
    if token:
        out.append(token)
    # Join keys with dots; leave index tokens bracketed.
    rendered = ""
    for piece in out:
        if piece.startswith("[") and piece.endswith("]"):
            rendered += piece
        elif rendered:
            rendered += f".{piece}"
        else:
            rendered += piece
    return rendered


def compute_diff(
    *,
    kind: str,
    name: str,
    folder: str,
    desired: dict[str, Any] | None,
    actual: dict[str, Any] | None,
) -> ObjectDiff:
    """Produce a single ObjectDiff for one (kind,name,folder) identity."""
    if desired is None and actual is None:
        raise ValueError("compute_diff needs at least one of desired/actual")

    if desired is None:
        return ObjectDiff(
            action="delete",
            kind=kind,
            name=name,
            folder=folder,
            desired=None,
            actual=actual,
        )
    if actual is None:
        return ObjectDiff(
            action="create",
            kind=kind,
            name=name,
            folder=folder,
            desired=desired,
            actual=None,
        )

    text_diff = DeepDiff(actual, desired, ignore_order=True, verbose_level=2)
    if not text_diff:
        return ObjectDiff(
            action="noop",
            kind=kind,
            name=name,
            folder=folder,
            desired=desired,
            actual=actual,
        )

    changes = _field_changes_from_deepdiff(text_diff)
    return ObjectDiff(
        action="update",
        kind=kind,
        name=name,
        folder=folder,
        desired=desired,
        actual=actual,
        changes=changes,
    )


__all__ = ["Action", "FieldChange", "ObjectDiff", "compute_diff"]
