"""Engine-layer exceptions with file:line context.

These are thrown by the loader and validator. The CLI converts them into
coloured diagnostics; tests raise and inspect them directly.
"""

from __future__ import annotations

from pathlib import Path


class EngineError(Exception):
    """Base class for loader/validator failures."""

    def __init__(
        self,
        message: str,
        *,
        source_path: Path | None = None,
        line: int | None = None,
    ) -> None:
        self.source_path = source_path
        self.line = line
        prefix = ""
        if source_path is not None:
            prefix = f"{source_path}"
            if line is not None:
                prefix += f":{line}"
            prefix += ": "
        super().__init__(f"{prefix}{message}")


class ManifestError(EngineError):
    """YAML parse failure or Pydantic validation failure on a single doc."""


class ValidationError(EngineError):
    """A cross-document validator rule was violated."""


class ReferenceError(ValidationError):
    """An ObjectRef didn't resolve to a declared manifest."""


__all__ = ["EngineError", "ManifestError", "ReferenceError", "ValidationError"]
