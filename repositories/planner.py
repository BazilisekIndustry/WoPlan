"""Persistence operations used by the UI. Domain calculations remain in services/."""
from __future__ import annotations

from supabase import Client


def list_workplaces(db: Client) -> list[dict]:
    return db.table("workplaces").select("*").eq("active", True).order("name").execute().data


def list_projects(db: Client) -> list[dict]:
    return db.table("projects").select("*").order("project_number").execute().data


def list_tasks(db: Client) -> list[dict]:
    return (db.table("tasks").select("*, projects(project_number,name), workplaces(name,hours_per_workday,working_days)")
            .order("planned_start").execute().data)


def list_dependencies(db: Client) -> list[dict]:
    return db.table("task_dependencies").select("*").execute().data


def create_project(db: Client, values: dict) -> dict:
    return db.table("projects").insert(values).execute().data[0]


def create_task(db: Client, values: dict) -> dict:
    return db.table("tasks").insert(values).execute().data[0]


def create_dependency(db: Client, values: dict) -> dict:
    return db.table("task_dependencies").insert(values).execute().data[0]


def update_task_status(db: Client, task_id: str, values: dict) -> None:
    db.table("tasks").update(values).eq("id", task_id).execute()


def update_task_metadata(db: Client, task_id: str, values: dict) -> None:
    """For non-scheduling fields only. Schedule dates always go through the RPC."""
    db.table("tasks").update(values).eq("id", task_id).execute()
