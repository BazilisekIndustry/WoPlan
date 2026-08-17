from __future__ import annotations

from datetime import date, timedelta
from models.domain import Workplace


def is_working_day(day: date, workplace: Workplace) -> bool:
    return day.weekday() in workplace.working_days


def next_working_day(day: date, workplace: Workplace) -> date:
    while not is_working_day(day, workplace):
        day += timedelta(days=1)
    return day


def add_working_days(start: date, days: int, workplace: Workplace, *, include_start: bool = False) -> date:
    """Move forward by working days. With include_start, day one is the start date."""
    if days < 0:
        return subtract_working_days(start, -days, workplace, include_start=include_start)
    current, remaining = start, days
    if include_start and is_working_day(current, workplace):
        remaining -= 1
    while remaining > 0:
        current += timedelta(days=1)
        if is_working_day(current, workplace):
            remaining -= 1
    return current


def subtract_working_days(start: date, days: int, workplace: Workplace, *, include_start: bool = False) -> date:
    if days < 0:
        return add_working_days(start, -days, workplace, include_start=include_start)
    current, remaining = start, days
    if include_start and is_working_day(current, workplace):
        remaining -= 1
    while remaining > 0:
        current -= timedelta(days=1)
        if is_working_day(current, workplace):
            remaining -= 1
    return current


def calculate_task_end(start: date, duration_workdays: int, workplace: Workplace) -> date:
    if duration_workdays <= 0:
        raise ValueError("Task duration must be greater than zero.")
    # A non-working selected start begins on the next available workday.
    start = next_working_day(start, workplace)
    return add_working_days(start, duration_workdays, workplace, include_start=True)


def working_days_between(start: date, end: date, workplace: Workplace, *, inclusive: bool = True) -> int:
    if end < start:
        return -working_days_between(end, start, workplace, inclusive=inclusive)
    days, current = 0, start
    final = end if inclusive else end - timedelta(days=1)
    while current <= final:
        days += is_working_day(current, workplace)
        current += timedelta(days=1)
    return days


def calculate_delay_workdays(planned_end: date, today: date, workplace: Workplace) -> int:
    if planned_end >= today:
        return 0
    return working_days_between(planned_end + timedelta(days=1), today, workplace)
