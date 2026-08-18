-- Atomic schedule mutation with optimistic locking. The browser/Streamlit client invokes this RPC.
create or replace function public.apply_schedule_change(p_changes jsonb, p_expected_updated_at jsonb)
returns void language plpgsql security invoker set search_path = public as $$
declare item jsonb; current_task public.tasks%rowtype; expected timestamptz;
begin
  if not public.is_admin() then raise exception 'Only administrators can modify schedules'; end if;
  -- Locks acquired in a stable UUID order, then every timestamp is checked before any update.
  perform 1 from public.tasks where id in (select (value->>'id')::uuid from jsonb_array_elements(p_changes)) order by id for update;
  for item in select value from jsonb_array_elements(p_changes) loop
    select * into current_task from public.tasks where id = (item->>'id')::uuid;
    if not found then raise exception 'Task not found'; end if;
    expected := (p_expected_updated_at ->> (item->>'id'))::timestamptz;
    if expected is null or current_task.updated_at <> expected then raise exception 'STALE_SCHEDULE: reload and try again'; end if;
  end loop;
  for item in select value from jsonb_array_elements(p_changes) loop
    select * into current_task from public.tasks where id = (item->>'id')::uuid;
    update public.tasks set planned_start=(item->>'planned_start')::date, planned_end=(item->>'planned_end')::date,
      duration_workdays=coalesce((item->>'duration_workdays')::integer, duration_workdays),
      workplace_id=coalesce((item->>'workplace_id')::uuid, workplace_id), updated_by=auth.uid()
      where id=current_task.id;
    -- General task audit trigger records the before/after row in the same transaction.
  end loop;
end $$;
