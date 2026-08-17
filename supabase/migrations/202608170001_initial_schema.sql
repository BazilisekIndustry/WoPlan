-- Project Planner: run this migration in an empty Supabase PostgreSQL project.
create extension if not exists pgcrypto;

create type public.app_role as enum ('admin', 'viewer');
create type public.project_status as enum ('active', 'completed', 'cancelled');
create type public.task_status as enum ('planned', 'in_progress', 'completed', 'cancelled');

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null, role public.app_role not null default 'viewer',
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table public.projects (
  id uuid primary key default gen_random_uuid(), project_number text not null unique, name text not null,
  description text, status public.project_status not null default 'active', planned_end date,
  created_at timestamptz not null default now(), created_by uuid references auth.users(id),
  updated_at timestamptz not null default now(), updated_by uuid references auth.users(id)
);
create table public.workplaces (
  id uuid primary key default gen_random_uuid(), name text not null unique, description text,
  hours_per_workday numeric(8,2) not null check (hours_per_workday > 0),
  working_days smallint[] not null check (cardinality(working_days) > 0 and working_days <@ array[0,1,2,3,4,5,6]::smallint[]),
  annual_capacity_hours numeric(12,2), active boolean not null default true,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table public.tasks (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete restrict,
  name text not null, workplace_id uuid not null references public.workplaces(id) on delete restrict,
  duration_workdays integer not null check (duration_workdays > 0), zt_count integer not null default 0 check (zt_count >= 0),
  planned_start date not null, planned_end date not null, actual_start timestamptz, actual_end timestamptz,
  status public.task_status not null default 'planned', created_at timestamptz not null default now(),
  created_by uuid references auth.users(id), updated_at timestamptz not null default now(), updated_by uuid references auth.users(id),
  check (planned_end >= planned_start)
);
create table public.task_dependencies (
  id uuid primary key default gen_random_uuid(), predecessor_task_id uuid not null references public.tasks(id) on delete restrict,
  successor_task_id uuid not null references public.tasks(id) on delete restrict,
  offset_workdays integer not null default 3 check (offset_workdays >= 0),
  created_at timestamptz not null default now(), created_by uuid references auth.users(id),
  unique (predecessor_task_id, successor_task_id), check (predecessor_task_id <> successor_task_id)
);
create table public.audit_log (
  id uuid primary key default gen_random_uuid(), user_id uuid references auth.users(id), timestamp timestamptz not null default now(),
  entity_type text not null, entity_id uuid not null, action text not null, old_data jsonb, new_data jsonb
);
create index tasks_project_id_idx on public.tasks(project_id); create index tasks_workplace_id_idx on public.tasks(workplace_id);
create index tasks_planned_start_idx on public.tasks(planned_start); create index tasks_planned_end_idx on public.tasks(planned_end);
create index tasks_status_idx on public.tasks(status); create index deps_predecessor_idx on public.task_dependencies(predecessor_task_id);
create index deps_successor_idx on public.task_dependencies(successor_task_id); create index audit_entity_idx on public.audit_log(entity_type, entity_id);

create or replace function public.touch_updated_at() returns trigger language plpgsql as $$ begin new.updated_at = now(); return new; end $$;
create trigger projects_touch before update on public.projects for each row execute function public.touch_updated_at();
create trigger tasks_touch before update on public.tasks for each row execute function public.touch_updated_at();
create trigger workplaces_touch before update on public.workplaces for each row execute function public.touch_updated_at();
create trigger profiles_touch before update on public.profiles for each row execute function public.touch_updated_at();

create or replace function public.is_admin() returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.profiles where id = auth.uid() and role = 'admin')
$$;
alter table public.profiles enable row level security; alter table public.projects enable row level security;
alter table public.workplaces enable row level security; alter table public.tasks enable row level security;
alter table public.task_dependencies enable row level security; alter table public.audit_log enable row level security;
create policy authenticated_read on public.profiles for select to authenticated using (true);
-- Profile roles are maintained through Supabase Dashboard/service role. A user must never edit their own role.
create policy profile_admin_update on public.profiles for update to authenticated using (public.is_admin()) with check (public.is_admin());
-- Read-only viewers; administrators retain full CRUD. Authenticated data is never public.
create policy authenticated_read on public.projects for select to authenticated using (true); create policy admin_write on public.projects for all to authenticated using (public.is_admin()) with check (public.is_admin());
create policy authenticated_read on public.workplaces for select to authenticated using (true); create policy admin_write on public.workplaces for all to authenticated using (public.is_admin()) with check (public.is_admin());
create policy authenticated_read on public.tasks for select to authenticated using (true); create policy admin_write on public.tasks for all to authenticated using (public.is_admin()) with check (public.is_admin());
create policy authenticated_read on public.task_dependencies for select to authenticated using (true); create policy admin_write on public.task_dependencies for all to authenticated using (public.is_admin()) with check (public.is_admin());
create policy audit_admin_read on public.audit_log for select to authenticated using (public.is_admin()); create policy audit_admin_write on public.audit_log for insert to authenticated with check (public.is_admin());

create or replace function public.audit_row_change() returns trigger language plpgsql security invoker set search_path = public as $$
begin
  if tg_op = 'INSERT' then
    insert into public.audit_log(user_id, entity_type, entity_id, action, new_data)
      values (auth.uid(), tg_table_name, new.id, upper(tg_table_name) || '_CREATED', to_jsonb(new));
    return new;
  elsif tg_op = 'UPDATE' then
    insert into public.audit_log(user_id, entity_type, entity_id, action, old_data, new_data)
      values (auth.uid(), tg_table_name, new.id, upper(tg_table_name) || '_UPDATED', to_jsonb(old), to_jsonb(new));
    return new;
  else
    insert into public.audit_log(user_id, entity_type, entity_id, action, old_data)
      values (auth.uid(), tg_table_name, old.id, upper(tg_table_name) || '_DELETED', to_jsonb(old));
    return old;
  end if;
end $$;
create trigger audit_projects after insert or update or delete on public.projects for each row execute function public.audit_row_change();
create trigger audit_tasks after insert or update or delete on public.tasks for each row execute function public.audit_row_change();
create trigger audit_dependencies after insert or update or delete on public.task_dependencies for each row execute function public.audit_row_change();
