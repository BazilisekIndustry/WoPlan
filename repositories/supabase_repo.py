from __future__ import annotations
import os
from dotenv import load_dotenv
from supabase import Client, create_client


load_dotenv()


def client(access_token: str | None = None, refresh_token: str | None = None) -> Client:
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_ANON_KEY")
    if not url or not key: raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY must be configured.")
    db = create_client(url, key)
    if access_token and refresh_token: db.auth.set_session(access_token, refresh_token)
    return db


def update_schedule_atomically(db: Client, changes: list[dict], expected_updated_at: dict[str, str]) -> object:
    """Calls a PostgreSQL RPC; create it in a follow-up migration for production deployments.
    The RPC must lock all task rows, compare expected timestamps, update all rows and audit them in one transaction.
    """
    return db.rpc("apply_schedule_change", {"p_changes": changes, "p_expected_updated_at": expected_updated_at}).execute()
