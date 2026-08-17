from __future__ import annotations

from datetime import date

from models.domain import Task, Workplace
from services.calendars import calculate_delay_workdays


def current_project_end(tasks: list[Task]) -> date | None:
    relevant = [task.planned_end for task in tasks if task.status != "cancelled"]
    return max(relevant) if relevant else None


def project_deadline_delay(tasks: list[Task], planned_end: date | None, workplaces: dict[object, Workplace]) -> int:
    current_end = current_project_end(tasks)
    if not planned_end or not current_end or current_end <= planned_end:
        return 0
    final_task = max((task for task in tasks if task.status != "cancelled"), key=lambda task: task.planned_end)
    return calculate_delay_workdays(planned_end, current_end, workplaces[final_task.workplace_id])
