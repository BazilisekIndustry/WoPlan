from datetime import date
import pytest
from models.domain import Dependency, Task, Workplace
from services.calendars import add_working_days, calculate_delay_workdays, calculate_task_end, next_working_day
from services.capacity import monthly_capacity_hours, planned_hours_in_month
from services.conflicts import conflicts, first_available_start
from services.scheduling import CircularDependencyError, dependency_start, move_task, revise_task, validate_acyclic
from services.projects import current_project_end, project_deadline_delay
from services.views import group_by_project, group_by_workplace, interval_bounds, visible_tasks
from services.plist_pdf import build_plist_pdf

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


def test_schedule_projections_share_filters_and_chronological_order():
    rows = [
        {"id": "2", "project_id": "p", "name": "Later", "workplace_id": "w", "planned_start": "2026-08-10", "planned_end": "2026-08-12", "requested_end": "2026-08-15", "status": "planned", "projects": {"project_number": "P-1", "name": "Pilot"}, "workplaces": {"name": "Chamber"}},
        {"id": "1", "project_id": "p", "name": "Earlier", "workplace_id": "w", "planned_start": "2026-08-03", "planned_end": "2026-08-05", "requested_end": "2026-08-06", "status": "planned", "projects": {"project_number": "P-1", "name": "Pilot"}, "workplaces": {"name": "Chamber"}},
        {"id": "3", "project_id": "p", "name": "Cancelled", "workplace_id": "w", "planned_start": "2026-08-04", "planned_end": "2026-08-05", "status": "cancelled", "projects": {"project_number": "P-1", "name": "Pilot"}, "workplaces": {"name": "Chamber"}},
    ]
    result = visible_tasks(rows, start=date(2026, 8, 1), end=date(2026, 8, 31))
    assert [row["id"] for row in result] == ["1", "2"]
    assert group_by_project(result)[0][1] == result
    assert group_by_workplace(result)[0][1] == result


def test_schedule_projection_includes_overlapping_and_one_sided_date_tasks():
    rows = [
        {"id": "overlap-start", "name": "Začíná dříve", "planned_start": "2026-08-01", "planned_end": "2026-08-12", "status": "planned"},
        {"id": "overlap-end", "name": "Končí později", "planned_start": "2026-08-10", "planned_end": "2026-08-25", "status": "planned"},
        {"id": "deadline", "name": "Jen termín", "requested_end": "2026-08-15", "status": "planned"},
        {"id": "unscheduled", "name": "Bez termínu", "status": "planned"},
    ]
    result = visible_tasks(rows, start=date(2026, 8, 10), end=date(2026, 8, 16))
    assert {row["id"] for row in result} == {"overlap-start", "overlap-end", "deadline"}
    assert interval_bounds(next(row for row in result if row["id"] == "deadline")) == (date(2026, 8, 15), date(2026, 8, 15))


def test_plist_pdf_is_created_for_empty_project_and_task_details():
    project = {"project_number": "P-1", "name": "Pilot", "description": "Demo"}
    task_rows = [{"id": "1", "project_id": "p", "name": "Test", "description": "Popis", "workplace_id": "w", "planned_start": "2026-08-03", "planned_end": "2026-08-05", "requested_end": "2026-08-06", "zt_count": 3, "status": "planned", "workplaces": {"name": "Chamber"}}]
    assert build_plist_pdf(project, [], date(2026, 8, 20)).startswith(b"%PDF")
    assert build_plist_pdf(project, task_rows, date(2026, 8, 20)).startswith(b"%PDF")
