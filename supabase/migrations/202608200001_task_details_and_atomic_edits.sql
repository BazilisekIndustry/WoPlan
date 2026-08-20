-- Task descriptions and requested completion dates are first-class planning data.
alter table public.tasks add column if not exists description text;
alter table public.tasks add column if not exists requested_end date;

-- One transaction remains the only writer for an edited plan.  Optional metadata
-- fields are accepted for the edited root task; propagated descendants only carry
-- their recalculated dates.
create or replace function public.apply_schedule_change(p_changes jsonb, p_expected_updated_at jsonb)
returns void language plpgsql security invoker set search_path = public as $$
declare item jsonb; current_task public.tasks%rowtype; expected timestamptz;
begin
  if not public.is_admin() then raise exception 'Only administrators can modify schedules'; end if;
  if jsonb_array_length(p_changes) = 0 then return; end if;
  perform 1 from public.tasks where id in (select (value->>'id')::uuid from jsonb_array_elements(p_changes)) order by id for update;
  for item in select value from jsonb_array_elements(p_changes) loop
    select * into current_task from public.tasks where id = (item->>'id')::uuid;
    if not found then raise exception 'Task not found'; end if;
    expected := (p_expected_updated_at ->> (item->>'id'))::timestamptz;
    if expected is null or current_task.updated_at <> expected then raise exception 'STALE_SCHEDULE: reload and try again'; end if;
    if item ? 'project_id' and not exists (select 1 from public.projects where id = (item->>'project_id')::uuid) then
      raise exception 'Project not found';
    end if;
    if item ? 'workplace_id' and not exists (select 1 from public.workplaces where id = (item->>'workplace_id')::uuid) then
      raise exception 'Workplace not found';
    end if;
    if item ? 'name' and btrim(item->>'name') = '' then raise exception 'Task name is required'; end if;
    if item ? 'duration_workdays' and (item->>'duration_workdays')::integer <= 0 then raise exception 'Task duration must be positive'; end if;
    if item ? 'zt_count' and (item->>'zt_count')::integer < 0 then raise exception 'ZT count must not be negative'; end if;
  end loop;
  for item in select value from jsonb_array_elements(p_changes) loop
    update public.tasks set
      planned_start=(item->>'planned_start')::date,
      planned_end=(item->>'planned_end')::date,
      duration_workdays=coalesce((item->>'duration_workdays')::integer, duration_workdays),
      workplace_id=coalesce((item->>'workplace_id')::uuid, workplace_id),
      project_id=coalesce((item->>'project_id')::uuid, project_id),
      name=coalesce(nullif(btrim(item->>'name'), ''), name),
      description=case when item ? 'description' then nullif(btrim(item->>'description'), '') else description end,
      requested_end=case when item ? 'requested_end' then nullif(item->>'requested_end', '')::date else requested_end end,
      zt_count=coalesce((item->>'zt_count')::integer, zt_count),
      updated_by=auth.uid()
      where id=(item->>'id')::uuid;
  end loop;
end $$;
