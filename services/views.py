"""Shared, read-only projections used by every schedule view and export."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Iterable


def relevant_date(task: dict) -> date:
    """Use the required completion date when it exists, otherwise schedule end.

    This is deliberately the single chronological ordering rule for the short-term
    plan, project details and PLIST export.
    """
    value = task.get("requested_end") or task.get("planned_end") or task.get("planned_start")
    if not value:
        return date.max
    return value if isinstance(value, date) else date.fromisoformat(value)


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
                  status: str | None = None) -> list[dict]:
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
    return sorted(result, key=lambda item: (relevant_date(item), interval_bounds(item)[0], item["name"].casefold(), str(item["id"])))


def group_by_workplace(tasks: Iterable[dict]) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for task in tasks:
        groups[(task.get("workplaces") or {}).get("name") or "Nepřiřazené pracoviště"].append(task)
    return [(name, sorted(items, key=lambda item: (*interval_bounds(item), item["name"].casefold())))
            for name, items in sorted(groups.items(), key=lambda item: item[0].casefold())]


def group_by_project(tasks: Iterable[dict]) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for task in tasks:
        project = task.get("projects") or {}
        name = f"{project.get('project_number') or 'Bez projektu'} - {project.get('name') or ''}".rstrip(" -")
        groups[name].append(task)
    return [(name, sorted(items, key=lambda item: (relevant_date(item), interval_bounds(item)[0], item["name"].casefold(), str(item["id"]))))
            for name, items in sorted(groups.items(), key=lambda item: item[0].casefold())]
