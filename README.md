# Project Planner

Focused planning for hot cells, chambers and technical workplaces. Supabase is the source of truth; `services/` is a deterministic, UI-independent scheduling engine; Streamlit is intentionally a small presentation layer.

## Architecture

- `supabase/migrations/`: schema, constraints, authenticated RLS and atomic schedule RPC.
- `models/`: immutable domain values.
- `services/`: calendar, dependency propagation, conflicts and capacity calculations.
- `repositories/`: Supabase access; multi-task schedule writes use `apply_schedule_change`, never independent updates.
- `app.py`: authenticated dashboard, compact weekly HMG, move-impact preview, project/task creation and annual capacity heatmap.

## Local setup

Requires Python 3.10+. Create a virtual environment, install `pip install -r requirements.txt`, copy `.env.example` to `.env`, then provide the Supabase URL and **anon** key only. Never expose a service-role key.

Apply the migrations in chronological order, including `202608200001_task_details_and_atomic_edits.sql`, in the Supabase SQL editor (or Supabase CLI). The last migration adds task descriptions and requested completion dates and extends the atomic scheduling RPC used by task editing. Create Auth users, then insert their `profiles` rows with `admin` or `viewer`. RLS permits all authenticated users to read planning data and only profile-backed admins to modify it.

To populate non-production data, run [supabase/seed_demo.sql](supabase/seed_demo.sql) after the migrations. It creates explicitly named `DEMO-*` projects, one 24-hour workplace, a dependency chain and an intentional Chamber 2 conflict.

Run `streamlit run app.py` and tests with `pytest`.

## Notes and next delivery increment

Moving a task previews all downstream shifts and every detected workplace conflict before the administrator confirms. It sends all resulting date changes plus their `updated_at` versions to `apply_schedule_change`, which locks, validates and writes them atomically.

The project detail includes a full task editor: project, workplace, duration, description, requested completion date and ZT are saved with the recalculated dependency branch in one transaction. The short-term plan provides synchronized views by workplace and project, while the project detail can export a printable PLIST PDF from the same task projection.

Optional demo seed data is intentionally not applied automatically; create it only after real workplace calendars are agreed.
