from __future__ import annotations

from calendar import monthrange
from datetime import date
from models.domain import Task, Workplace
from services.calendars import is_working_day


def monthly_capacity_hours(workplace: Workplace, year: int, month: int) -> float:
    return sum(workplace.hours_per_workday for d in range(1, monthrange(year, month)[1] + 1)
               if is_working_day(date(year, month, d), workplace))


def planned_hours_in_month(tasks: list[Task], workplace: Workplace, year: int, month: int) -> float:
    total = 0.0
    for task in tasks:
        if task.workplace_id != workplace.id or task.status == "cancelled":
            continue
        for day in range(1, monthrange(year, month)[1] + 1):
            current = date(year, month, day)
            if task.planned_start <= current <= task.planned_end and is_working_day(current, workplace):
                total += workplace.hours_per_workday
    return total
