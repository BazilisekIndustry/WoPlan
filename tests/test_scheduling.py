from datetime import date
import pytest
from models.domain import Dependency, Task, Workplace
from services.calendars import add_working_days, calculate_delay_workdays, calculate_task_end, next_working_day
from services.capacity import monthly_capacity_hours, planned_hours_in_month
from services.conflicts import conflicts, first_available_start
from services.scheduling import CircularDependencyError, dependency_start, move_task, revise_task, validate_acyclic
from services.projects import current_project_end, project_deadline_delay

WEEKDAY = Workplace("w1", "Chamber", 8, frozenset(range(5)))
ALL_DAYS = Workplace("w2", "24h", 24, frozenset(range(7)))

def task(id, workplace="w1", start=date(2026, 8, 3), duration=5, project="p1"):
    wp = WEEKDAY if workplace == "w1" else ALL_DAYS
    return Task(id, project, id, workplace, duration, start, calculate_task_end(start, duration, wp))

def test_working_day_and_end_calculations():
    assert add_working_days(date(2026, 8, 3), 5, WEEKDAY) == date(2026, 8, 10)
    assert calculate_task_end(date(2026, 8, 6), 3, WEEKDAY) == date(2026, 8, 10)
    assert calculate_task_end(date(2026, 8, 6), 3, ALL_DAYS) == date(2026, 8, 8)
    assert next_working_day(date(2026, 8, 8), WEEKDAY) == date(2026, 8, 10)

def test_delay_uses_workplace_calendar():
    assert calculate_delay_workdays(date(2026, 8, 20), date(2026, 8, 24), WEEKDAY) == 2
    assert calculate_delay_workdays(date(2026, 8, 20), date(2026, 8, 24), ALL_DAYS) == 4

def test_dependency_offset_uses_successor_calendar():
    predecessor = task("a", start=date(2026, 8, 3))  # ends Friday 7 August
    assert dependency_start(predecessor, Dependency("a", "b", 3), WEEKDAY) == date(2026, 8, 12)

def test_propagation_preserves_each_downstream_relative_position():
    a = task("a", start=date(2026, 8, 3)); b = task("b", start=date(2026, 8, 14)); c = task("c", start=date(2026, 8, 25))
    result = {x.id: x for x in move_task([a,b,c], [Dependency("a","b"), Dependency("b","c")], {"w1": WEEKDAY}, "a", date(2026,8,6))}
    assert result["a"].planned_start == date(2026,8,6)
    assert result["b"].planned_start == date(2026,8,19)
    assert result["c"].planned_start == date(2026,8,28)

def test_cycle_rejected():
    tasks = [task("a"), task("b"), task("c")]
    with pytest.raises(CircularDependencyError): validate_acyclic(tasks, [Dependency("a","b"), Dependency("b","c"), Dependency("c","a")])

def test_conflicts_only_same_workplace():
    a = task("a", start=date(2026,8,3)); b = task("b", start=date(2026,8,5)); c = task("c", workplace="w2", start=date(2026,8,5))
    assert len(conflicts([a,b,c])) == 1

def test_first_available_start_skips_full_conflicting_duration():
    blocker = task("blocker", start=date(2026, 8, 3), duration=5)
    moving = task("moving", start=date(2026, 8, 3), duration=3, project="p2")
    assert first_available_start(moving, date(2026, 8, 3), WEEKDAY, [blocker, moving]) == date(2026, 8, 10)

def test_propagation_can_create_cross_project_conflict():
    a = task("a", start=date(2026,8,3)); b = task("b", start=date(2026,8,12)); blocker = task("x", start=date(2026,8,17), project="p2")
    moved = move_task([a,b,blocker], [Dependency("a","b")], {"w1": WEEKDAY}, "a", date(2026,8,6))
    assert any({c.first_task_id,c.second_task_id} == {"b","x"} for c in conflicts(moved))

def test_capacity_for_8h_and_24h_workplace():
    assert monthly_capacity_hours(WEEKDAY, 2026, 2) == 160
    assert monthly_capacity_hours(ALL_DAYS, 2026, 2) == 672
    assert planned_hours_in_month([task("a")], WEEKDAY, 2026, 8) == 40

def test_project_deadline_calculation_ignores_cancelled_tasks():
    a = task("a", start=date(2026, 8, 3)); b = task("b", start=date(2026, 8, 17))
    assert current_project_end([a, b]) == date(2026, 8, 21)
    assert project_deadline_delay([a, b], date(2026, 8, 14), {"w1": WEEKDAY}) == 5

def test_duration_revision_recalculates_end_and_downstream_branch():
    a = task("a", start=date(2026, 8, 3), duration=5); b = task("b", start=date(2026, 8, 14))
    revised = {t.id: t for t in revise_task([a, b], [Dependency("a", "b")], {"w1": WEEKDAY}, "a", 7, "w1", a.planned_start)}
    assert revised["a"].planned_end == date(2026, 8, 11)
    assert revised["b"].planned_start == date(2026, 8, 18)
