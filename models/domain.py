from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID


@dataclass(frozen=True)
class Workplace:
    id: str | UUID
    name: str
    hours_per_workday: float
    working_days: frozenset[int]


@dataclass(frozen=True)
class Task:
    id: str | UUID
    project_id: str | UUID
    name: str
    workplace_id: str | UUID
    duration_workdays: int
    planned_start: date
    planned_end: date
    status: str = "planned"


@dataclass(frozen=True)
class Dependency:
    predecessor_task_id: str | UUID
    successor_task_id: str | UUID
    offset_workdays: int = 3


@dataclass(frozen=True)
class Conflict:
    workplace_id: str | UUID
    first_task_id: str | UUID
    second_task_id: str | UUID
    start: date
    end: date
