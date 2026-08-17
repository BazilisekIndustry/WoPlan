from __future__ import annotations

from collections import defaultdict
from datetime import date
from models.domain import Conflict, Task


def conflicts(tasks: list[Task]) -> list[Conflict]:
    result: list[Conflict] = []
    by_workplace: dict[object, list[Task]] = defaultdict(list)
    for task in tasks:
        if task.status != "cancelled":
            by_workplace[task.workplace_id].append(task)
    for workplace_id, items in by_workplace.items():
        ordered = sorted(items, key=lambda t: t.planned_start)
        for i, first in enumerate(ordered):
            for second in ordered[i + 1 :]:
                if second.planned_start > first.planned_end:
                    break
                start, end = max(first.planned_start, second.planned_start), min(first.planned_end, second.planned_end)
                if start <= end:
                    result.append(Conflict(workplace_id, first.id, second.id, start, end))
    return result


def nearest_available_start(task: Task, occupied: list[Task]) -> date:
    """Return first calendar day after all clashes; caller recalculates task end."""
    candidate = task.planned_start
    for other in sorted(occupied, key=lambda t: t.planned_start):
        if other.id != task.id and other.planned_start <= task.planned_end and other.planned_end >= candidate:
            candidate = other.planned_end.fromordinal(other.planned_end.toordinal() + 1)
    return candidate
