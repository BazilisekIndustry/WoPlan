"""Persistence operations used by the UI. Domain calculations remain in services/."""
from __future__ import annotations

from supabase import Client


def list_workplaces(db: Client) -> list[dict]:
    return db.table("workplaces").select("*").eq("active", True).order("name").execute().data


def list_all_workplaces(db: Client) -> list[dict]:
    return db.table("workplaces").select("*").order("active", desc=True).order("name").execute().data


def create_workplace(db: Client, values: dict) -> dict:
    return db.table("workplaces").insert(values).execute().data[0]


def set_workplace_active(db: Client, workplace_id: str, active: bool) -> None:
    """Deactivate/reactivate only; do not delete workplaces with historical tasks."""
    db.table("workplaces").update({"active": active}).eq("id", workplace_id).execute()


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
    response = db.table("tasks").insert(values).execute()
    if not response.data:
        raise RuntimeError("Supabase nepotvrdil vytvoření úlohy návratovými daty.")
    return response.data[0]


def create_dependency(db: Client, values: dict) -> dict:
    return db.table("task_dependencies").insert(values).execute().data[0]


def update_task_status(db: Client, task_id: str, values: dict) -> None:
    db.table("tasks").update(values).eq("id", task_id).execute()


def update_task_metadata(db: Client, task_id: str, values: dict) -> None:
    """For non-scheduling fields only. Schedule dates always go through the RPC."""
    db.table("tasks").update(values).eq("id", task_id).execute()


def list_project_samples(db: Client, project_id: str, *, status: str | None = None, search: str | None = None, limit: int = 500) -> list[dict]:
    query = db.table("samples").select("*, parent:parent_sample_id(code)").eq("project_id", project_id)
    if status and status != "all": query = query.eq("status", status)
    if search: query = query.ilike("code", f"%{search.strip()}%")
    return query.order("status").order("code").limit(limit).execute().data


def create_sample(db: Client, values: dict) -> dict:
    return db.table("samples").insert(values).execute().data[0]


def update_sample(db: Client, sample_id: str, values: dict) -> None:
    db.table("samples").update(values).eq("id", sample_id).execute()


def set_task_samples(db: Client, task_id: str, scope: str, sample_ids: list[str]) -> None:
    db.rpc("set_task_samples", {"p_task_id": task_id, "p_scope": scope, "p_sample_ids": sample_ids}).execute()


def split_sample(db: Client, sample_id: str, children: list[dict]) -> None:
    db.rpc("split_sample", {"p_sample_id": sample_id, "p_children": children}).execute()


def list_task_sample_ids(db: Client, task_id: str) -> list[str]:
    return [row["sample_id"] for row in db.table("task_samples").select("sample_id").eq("task_id", task_id).execute().data]


def import_project_samples(db: Client, project_id: str, rows: list[dict]) -> None:
    db.rpc("import_project_samples", {"p_project_id": project_id, "p_rows": rows}).execute()


def resolve_task_sample_codes(db: Client, project_id: str, tasks: list[dict]) -> dict[str, list[str]]:
    """Resolve dynamic ALL scopes for a report without exposing internal IDs."""
    active_codes = [row["code"] for row in list_project_samples(db, project_id, status="active", limit=10000)]
    result: dict[str, list[str]] = {}
    for task in tasks:
        if task.get("sample_scope") == "ALL":
            result[str(task["id"])] = active_codes
        else:
            rows = db.table("task_samples").select("samples(code)").eq("task_id", task["id"]).execute().data
            result[str(task["id"])] = sorted(row["samples"]["code"] for row in rows if row.get("samples"))
    return result
