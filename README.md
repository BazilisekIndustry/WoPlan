# Project Planner

Focused planning for hot cells, chambers and technical workplaces. Supabase is the source of truth; `services/` is a deterministic, UI-independent scheduling engine; Streamlit is intentionally a small presentation layer.

## Architecture

- `supabase/migrations/`: schema, constraints, authenticated RLS and atomic schedule RPC.
- `models/`: immutable domain values.
- `services/`: calendar, dependency propagation, conflicts and capacity calculations.
- `repositories/`: Supabase access; multi-task schedule writes use `apply_schedule_change`, never independent updates.
- `app.py`: authenticated dashboard, compact weekly HMG, move-impact preview, project/task creation and annual capacity heatmap.

## Local setup

Requires Python 3.11+. Create a virtual environment, install `pip install -r requirements.txt`, copy `.env.example` to `.env`, then provide the Supabase URL and **anon** key only. Never expose a service-role key.

Apply `supabase/migrations/202608170001_initial_schema.sql` then `202608170002_schedule_transaction.sql` in the Supabase SQL editor (or Supabase CLI). Create Auth users, then insert their `profiles` rows with `admin` or `viewer`. RLS permits all authenticated users to read planning data and only profile-backed admins to modify it.

Run `streamlit run app.py` and tests with `pytest`.

## Notes and next delivery increment

Moving a task previews all downstream shifts and every detected workplace conflict before the administrator confirms. It sends all resulting date changes plus their `updated_at` versions to `apply_schedule_change`, which locks, validates and writes them atomically.

The current UI deliberately keeps editing focused: project/task creation and task movement are implemented, while task-detail dialogs, changes to workplace/duration and explicit conflict-resolution proposals remain the next iteration. The domain services already provide the basis for those flows.

Optional demo seed data is intentionally not applied automatically; create it only after real workplace calendars are agreed.
