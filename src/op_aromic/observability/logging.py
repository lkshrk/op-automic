"""Structured logging configuration with secret redaction.

This module supersedes the old ``op_aromic.logging`` helper. It offers:

* ``configure_logging(level=..., format=...)`` — one entry point used by the
  CLI's global callback and by tests that need deterministic output.
* A processor that redacts any dict value whose key matches a well-known
  secret name (``authorization``, ``password``, ``token``, ``secret``,
  ``automic_password``, ``x-api-key``) as well as values shaped like a
  ``Bearer <token>`` string regardless of key name.
* A JSON renderer emitted when ``format="json"`` OR when ``CI=true`` is in
  the environment, so CI log collectors always receive parseable events.

Everything writes to ``stderr`` so that stdout stays reserved for human /
JSON command output produced by the CLI commands themselves.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import Any, Literal, cast

import structlog
from structlog.stdlib import BoundLogger
from structlog.types import EventDict, Processor, WrappedLogger

# Keys whose values should be replaced with ``_REDACTED``. Comparison is
# case-insensitive and matches whole tokens — e.g. ``authorization`` matches
# ``Authorization`` but not ``authorization_policy``.
_REDACT_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "password",
        "token",
        "secret",
        "automic_password",
        "x-api-key",
    },
)

_REDACTED = "***REDACTED***"

# Values shaped like ``Bearer <token>`` or ``Token <token>`` — caught even
# when the key is not on the redaction list, because operators sometimes log
# whole request dicts verbatim.
_BEARER_RE = re.compile(r"^\s*(Bearer|Token)\s+\S+", re.IGNORECASE)


def _should_redact_key(key: str) -> bool:
    return key.lower() in _REDACT_KEYS


def _redact_value(value: Any) -> Any:
    """Walk dict/list/str structures replacing secret-ish values in place.

    Returns a new object rather than mutating — the processor chain owns
    event dicts and other processors may still reference the original shape.
    """
    if isinstance(value, dict):
        return {k: _redact_pair(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    if isinstance(value, str) and _BEARER_RE.match(value):
        return _REDACTED
    return value


def _redact_pair(key: str, value: Any) -> Any:
    if isinstance(key, str) and _should_redact_key(key):
        return _REDACTED
    return _redact_value(value)


def redact_secrets(
    _logger: WrappedLogger, _method: str, event_dict: EventDict,
) -> EventDict:
    """structlog processor: redact secrets in the top-level event dict."""
    return {k: _redact_pair(k, v) for k, v in event_dict.items()}


def _is_ci() -> bool:
    return os.environ.get("CI", "").lower() in ("true", "1", "yes")


_LEVEL_MAP: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def _resolve_level(level: str) -> int:
    return _LEVEL_MAP.get(level.lower(), logging.INFO)


def configure_logging(
    *,
    level: str = "info",
    format: Literal["text", "json"] = "text",
) -> None:
    """Configure structlog + stdlib logging with redaction baked in.

    ``level`` is a stdlib-style string (``debug``/``info``/...). ``format``
    is ``"text"`` for the human console renderer or ``"json"`` for
    line-delimited JSON. ``CI=true`` forces JSON regardless of the flag.
    """
    effective_format: Literal["text", "json"] = (
        "json" if _is_ci() else format
    )

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", key="timestamp"),
        redact_secrets,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Processor
    if effective_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(_resolve_level(level)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> BoundLogger:
    """Thin wrapper so callers don't import structlog directly."""
    return cast(BoundLogger, structlog.get_logger(name) if name else structlog.get_logger())


__all__ = ["configure_logging", "get_logger", "redact_secrets"]
