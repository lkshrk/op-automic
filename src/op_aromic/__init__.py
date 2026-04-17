"""op-aromic: GitOps toolkit for Broadcom Automic Workload Automation.

Two supported entry points:

* **CLI** — ``aromic`` command, see :mod:`op_aromic.cli.app`.
* **Library** — import :mod:`op_aromic.api` or use the re-exports here::

      from op_aromic import api

      with api.open_client() as client:
          loaded = api.load("manifests/")
          result = api.apply(api.plan(loaded, client=client), client=client)

Top-level re-exports cover the most common embedding scenarios. For
full surface area import :mod:`op_aromic.api` directly.
"""

from op_aromic import api
from op_aromic.api import (
    ApiError,
    ApplyResult,
    AutomicAPI,
    AutomicClient,
    AutomicSettings,
    DestroyResult,
    ExportResult,
    Issue,
    LedgerRow,
    LoadedManifest,
    Manifest,
    Metadata,
    ObjectDiff,
    Plan,
    RollbackFailed,
    RollbackPlan,
    RollbackUnresolved,
    Severity,
    Status,
    ValidationFailed,
    ValidationReport,
    apply,
    compute_revision,
    destroy,
    export,
    history,
    load,
    open_client,
    plan,
    plan_and_apply,
    rollback,
    rollback_plan,
    validate,
)

__version__ = "0.1.0"

__all__ = [
    "ApiError",
    "ApplyResult",
    "AutomicAPI",
    "AutomicClient",
    "AutomicSettings",
    "DestroyResult",
    "ExportResult",
    "Issue",
    "LedgerRow",
    "LoadedManifest",
    "Manifest",
    "Metadata",
    "ObjectDiff",
    "Plan",
    "RollbackFailed",
    "RollbackPlan",
    "RollbackUnresolved",
    "Severity",
    "Status",
    "ValidationFailed",
    "ValidationReport",
    "__version__",
    "api",
    "apply",
    "compute_revision",
    "destroy",
    "export",
    "history",
    "load",
    "open_client",
    "plan",
    "plan_and_apply",
    "rollback",
    "rollback_plan",
    "validate",
]
