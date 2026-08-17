from __future__ import annotations

from dataclasses import replace
from datetime import date
from collections import defaultdict, deque
from models.domain import Dependency, Task, Workplace
from services.calendars import add_working_days, calculate_task_end, next_working_day, working_days_between


class CircularDependencyError(ValueError):
    pass


def validate_acyclic(tasks: list[Task], dependencies: list[Dependency]) -> None:
    ids = {t.id for t in tasks}; children = defaultdict(list); degree = {id: 0 for id in ids}
    for dep in dependencies:
        if dep.predecessor_task_id == dep.successor_task_id:
            raise CircularDependencyError("Cannot create dependency because it would create a circular dependency.")
        if dep.predecessor_task_id not in ids or dep.successor_task_id not in ids:
            raise ValueError("Dependency references an unknown task.")
        children[dep.predecessor_task_id].append(dep.successor_task_id); degree[dep.successor_task_id] += 1
    queue = deque(key for key, value in degree.items() if value == 0); count = 0
    while queue:
        node = queue.popleft(); count += 1
        for child in children[node]:
            degree[child] -= 1
            if degree[child] == 0: queue.append(child)
    if count != len(ids):
        raise CircularDependencyError("Cannot create dependency because it would create a circular dependency.")


def dependency_start(predecessor: Task, dependency: Dependency, successor_workplace: Workplace) -> date:
    """The successor's own workplace calendar governs its preparation/transfer offset."""
    return add_working_days(predecessor.planned_end, dependency.offset_workdays, successor_workplace)


def move_task(tasks: list[Task], dependencies: list[Dependency], workplaces: dict[object, Workplace], task_id: object, new_start: date) -> list[Task]:
    """Return a complete proposed schedule. Nothing is persisted here (atomic caller owns commit)."""
    validate_acyclic(tasks, dependencies)
    by_id = {task.id: task for task in tasks}
    if task_id not in by_id: raise ValueError("Task not found.")
    original = by_id[task_id]; root_wp = workplaces[original.workplace_id]
    delta = working_days_between(original.planned_start, new_start, root_wp, inclusive=False)
    # Moving by calendar-compatible working day count preserves existing downstream spacing.
    moved = dict(by_id); queue = deque([task_id]); seen = set()
    children = defaultdict(list)
    for dep in dependencies: children[dep.predecessor_task_id].append(dep.successor_task_id)
    while queue:
        current_id = queue.popleft()
        if current_id in seen: continue
        seen.add(current_id); current = moved[current_id]; wp = workplaces[current.workplace_id]
        start = next_working_day(new_start, wp) if current_id == task_id else add_working_days(current.planned_start, delta, wp)
        end = calculate_task_end(start, current.duration_workdays, wp)
        moved[current_id] = replace(current, planned_start=start, planned_end=end)
        queue.extend(children[current_id])
    return [moved[t.id] for t in tasks]
