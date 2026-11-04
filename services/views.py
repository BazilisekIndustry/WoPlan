"""Shared, read-only projections used by every schedule view and export."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import heapq
from typing import Iterable


def relevant_date(task: dict) -> date:
    """Use the calculated schedule completion date for chronological ordering.

    This is deliberately the single chronological ordering rule for the short-term
    plan, project details and PLIST export.
    """
    value = task.get("planned_end") or task.get("planned_start") or task.get("requested_end")
    if not value:
        return date.max
    return value if isinstance(value, date) else date.fromisoformat(value)


def chronological_tasks(tasks: Iterable[dict], dependencies: Iterable[dict] = ()) -> list[dict]:
    """Sort by planned finish while keeping each dependency after its parent."""
    items = list(tasks)
    by_id = {str(task["id"]): task for task in items}
    children: dict[str, list[str]] = defaultdict(list)
    indegree = {task_id: 0 for task_id in by_id}
    for dependency in dependencies:
        parent = str(dependency.get("predecessor_task_id", dependency.get("predecessor_id", "")))
        child = str(dependency.get("successor_task_id", dependency.get("successor_id", "")))
        if parent in by_id and child in by_id:
            children[parent].append(child)
            indegree[child] += 1
    def key(task_id: str):
        task = by_id[task_id]
        return (relevant_date(task), task.get("name", "").casefold(), task_id)
    ready = [(key(task_id), task_id) for task_id, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    result = []
    while ready:
        _, task_id = heapq.heappop(ready)
        result.append(by_id[task_id])
        for child in children[task_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(ready, (key(child), child))
    # Defensive fallback for malformed cyclic dependency data.
    if len(result) != len(items):
        included = {str(task["id"]) for task in result}
        result.extend(sorted((task for task in items if str(task["id"]) not in included),
                             key=lambda task: (relevant_date(task), task.get("name", "").casefold(), str(task["id"]))))
    return result


def interval_bounds(task: dict) -> tuple[date, date] | None:
    """Return a drawable planning interval, including one-sided date records."""
    start_value, end_value = task.get("planned_start"), task.get("planned_end")
    start = start_value if isinstance(start_value, date) else date.fromisoformat(start_value) if start_value else None
    end = end_value if isinstance(end_value, date) else date.fromisoformat(end_value) if end_value else None
    if not start and not end:
        # A requested completion date still has a meaningful point on the timeline.
        point = task.get("requested_end")
        point = point if isinstance(point, date) else date.fromisoformat(point) if point else None
        return (point, point) if point else None
    return (start or end, end or start)


def visible_tasks(tasks: Iterable[dict], *, start: date | None = None, end: date | None = None,
                  project_id: object | None = None, workplace_id: object | None = None,
                  status: str | None = None, dependencies: Iterable[dict] = ()) -> list[dict]:
    """Filter consistently without mutating the Supabase source records."""
    result = []
    for task in tasks:
        if task.get("status") == "cancelled":
            continue
        if project_id is not None and task.get("project_id") != project_id:
            continue
        if workplace_id is not None and task.get("workplace_id") != workplace_id:
            continue
        if status and task.get("status") != status:
            continue
        interval = interval_bounds(task)
        if not interval:
            continue
        task_start, task_end = interval
        if start and task_end < start:
            continue
        if end and task_start > end:
            continue
        result.append(task)
    return chronological_tasks(result, dependencies)


def group_by_workplace(tasks: Iterable[dict], dependencies: Iterable[dict] = ()) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for task in tasks:
        groups[(task.get("workplaces") or {}).get("name") or "Nepřiřazené pracoviště"].append(task)
    return [(name, chronological_tasks(items, dependencies))
            for name, items in sorted(groups.items(), key=lambda item: item[0].casefold())]


def group_by_project(tasks: Iterable[dict], dependencies: Iterable[dict] = ()) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for task in tasks:
        project = task.get("projects") or {}
        name = f"{project.get('project_number') or 'Bez projektu'} - {project.get('name') or ''}".rstrip(" -")
        groups[name].append(task)
    return [(name, chronological_tasks(items, dependencies))
            for name, items in sorted(groups.items(), key=lambda item: item[0].casefold())]
