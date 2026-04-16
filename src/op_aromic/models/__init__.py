"""Manifest models and per-kind spec registry.

Importing this package has the side effect of registering every known
`kind` (Workflow, Job, Schedule, Calendar, Variable) into KIND_REGISTRY.
"""

from __future__ import annotations

from op_aromic.models.base import KIND_REGISTRY, Manifest, Metadata, ObjectRef
from op_aromic.models.calendar import CalendarSpec
from op_aromic.models.job import JobSpec
from op_aromic.models.schedule import ScheduleSpec
from op_aromic.models.variable import VariableSpec
from op_aromic.models.workflow import WorkflowSpec, WorkflowTask

__all__ = [
    "KIND_REGISTRY",
    "CalendarSpec",
    "JobSpec",
    "Manifest",
    "Metadata",
    "ObjectRef",
    "ScheduleSpec",
    "VariableSpec",
    "WorkflowSpec",
    "WorkflowTask",
]
