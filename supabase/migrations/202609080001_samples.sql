-- Samples are owned by a project, but their external code is global.
alter table public.tasks add column if not exists sample_scope text not null default 'SELECTED'
  check (sample_scope in ('ALL', 'SELECTED'));
alter table public.tasks add column if not exists legacy_zt_count integer;
update public.tasks set legacy_zt_count = zt_count where legacy_zt_count is null;

create table if not exists public.samples (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete restrict,
  code text not null unique check (code = btrim(code) and code <> ''),
  description text, type text,
  status text not null default 'active' check (status in ('active', 'split', 'inactive')),
  parent_sample_id uuid references public.samples(id) on delete restrict,
  created_at timestamptz not null default now(), created_by uuid references auth.users(id),
  updated_at timestamptz not null default now(), updated_by uuid references auth.users(id),
  check (parent_sample_id is null or parent_sample_id <> id)
);
create table if not exists public.task_samples (
  task_id uuid not null references public.tasks(id) on delete restrict,
  sample_id uuid not null references public.samples(id) on delete restrict,
  primary key (task_id, sample_id)
);
create index if not exists samples_project_id_idx on public.samples(project_id);
create index if not exists samples_status_idx on public.samples(status);
create index if not exists samples_parent_sample_id_idx on public.samples(parent_sample_id);
create index if not exists task_samples_sample_id_idx on public.task_samples(sample_id);
create index if not exists task_samples_task_id_idx on public.task_samples(task_id);

create or replace function public.validate_sample_lineage() returns trigger language plpgsql as $$
begin
  if new.parent_sample_id is not null and not exists (
    select 1 from public.samples where id = new.parent_sample_id and project_id = new.project_id
  ) then raise exception 'Parent sample must belong to the same project'; end if;
  return new;
end $$;
drop trigger if exists samples_validate_lineage on public.samples;
create trigger samples_validate_lineage before insert or update of project_id,parent_sample_id on public.samples for each row execute function public.validate_sample_lineage();
create trigger samples_touch before update on public.samples for each row execute function public.touch_updated_at();
create trigger audit_samples after insert or update or delete on public.samples for each row execute function public.audit_row_change();

create or replace function public.validate_task_sample_project() returns trigger language plpgsql as $$
begin
  if not exists (select 1 from public.tasks t join public.samples s on s.id = new.sample_id where t.id = new.task_id and t.project_id = s.project_id) then
    raise exception 'Task and sample must belong to the same project';
  end if;
  return new;
end $$;
drop trigger if exists task_samples_validate_project on public.task_samples;
create trigger task_samples_validate_project before insert or update on public.task_samples for each row execute function public.validate_task_sample_project();

create or replace function public.validate_task_project_change() returns trigger language plpgsql as $$
begin
 if new.project_id <> old.project_id and exists (
   select 1 from public.task_samples ts join public.samples s on s.id=ts.sample_id
   where ts.task_id=new.id and s.project_id<>new.project_id
 ) then raise exception 'Cannot move a task to another project while it has sample associations'; end if;
 return new;
end $$;
drop trigger if exists tasks_validate_project_change on public.tasks;
create trigger tasks_validate_project_change before update of project_id on public.tasks
for each row execute function public.validate_task_project_change();

alter table public.samples enable row level security;
alter table public.task_samples enable row level security;
create policy samples_authenticated_read on public.samples for select to authenticated using (true);
create policy samples_admin_write on public.samples for all to authenticated using (public.is_admin()) with check (public.is_admin());
create policy task_samples_authenticated_read on public.task_samples for select to authenticated using (true);
create policy task_samples_admin_write on public.task_samples for all to authenticated using (public.is_admin()) with check (public.is_admin());

create or replace function public.sync_task_zt_count(p_task_id uuid) returns void language plpgsql security invoker set search_path = public as $$
begin
 update public.tasks t set zt_count = case when t.sample_scope = 'ALL' then
   (select count(*) from public.samples s where s.project_id=t.project_id and s.status='active')
   else (select count(*) from public.task_samples ts where ts.task_id=t.id) end
 where t.id=p_task_id;
end $$;
create or replace function public.task_samples_sync_zt() returns trigger language plpgsql as $$
begin perform public.sync_task_zt_count(coalesce(new.task_id, old.task_id)); return coalesce(new, old); end $$;
drop trigger if exists task_samples_sync_zt on public.task_samples;
create trigger task_samples_sync_zt after insert or delete on public.task_samples for each row execute function public.task_samples_sync_zt();

-- ZT is a projection of samples, never a separately editable planning input.
create or replace function public.derive_task_zt_count() returns trigger language plpgsql as $$
begin
  new.zt_count := case when new.sample_scope='ALL' then
    (select count(*) from public.samples where project_id=new.project_id and status='active')
    else (select count(*) from public.task_samples where task_id=new.id) end;
  return new;
end $$;
drop trigger if exists tasks_derive_zt on public.tasks;
create trigger tasks_derive_zt before insert or update of project_id,sample_scope,zt_count on public.tasks
for each row execute function public.derive_task_zt_count();

create or replace function public.samples_sync_all_task_counts() returns trigger language plpgsql as $$
begin
  perform public.sync_task_zt_count(id) from public.tasks where project_id=coalesce(new.project_id,old.project_id) and sample_scope='ALL';
  return coalesce(new,old);
end $$;
drop trigger if exists samples_sync_all_zt on public.samples;
create trigger samples_sync_all_zt after insert or update of status,project_id or delete on public.samples
for each row execute function public.samples_sync_all_task_counts();

-- Replaces a selection in one transaction.  It is also the API used by task creation/editing.
create or replace function public.set_task_samples(p_task_id uuid, p_scope text, p_sample_ids uuid[]) returns void language plpgsql security invoker set search_path = public as $$
declare task_project uuid; bad_count integer;
begin
 if not public.is_admin() then raise exception 'Only administrators can change samples'; end if;
 if p_scope not in ('ALL','SELECTED') then raise exception 'Invalid sample scope'; end if;
 select project_id into task_project from public.tasks where id=p_task_id for update;
 if task_project is null then raise exception 'Task not found'; end if;
 if p_scope='SELECTED' then
   if (select count(*) from unnest(coalesce(p_sample_ids, '{}'::uuid[]))) <> (select count(distinct id) from unnest(coalesce(p_sample_ids, '{}'::uuid[])) x(id)) then
     raise exception 'Duplicate selected sample';
   end if;
   select count(*) into bad_count from unnest(coalesce(p_sample_ids, '{}'::uuid[])) x(id)
     left join public.samples s on s.id=x.id where s.id is null or s.project_id<>task_project or s.status<>'active';
   if bad_count > 0 then raise exception 'Only active samples of the task project may be selected'; end if;
 end if;
 update public.tasks set sample_scope=p_scope, updated_by=auth.uid() where id=p_task_id;
 delete from public.task_samples where task_id=p_task_id;
 if p_scope='SELECTED' then insert into public.task_samples(task_id,sample_id)
   select p_task_id, id from unnest(coalesce(p_sample_ids, '{}'::uuid[])) id; end if;
 perform public.sync_task_zt_count(p_task_id);
 insert into public.audit_log(user_id,entity_type,entity_id,action,new_data) values
  (auth.uid(),'task_samples',p_task_id,'TASK_SAMPLES_REPLACED',jsonb_build_object('scope',p_scope,'sample_ids',coalesce(p_sample_ids,'{}'::uuid[])));
end $$;

create or replace function public.import_project_samples(p_project_id uuid, p_rows jsonb) returns void language plpgsql security invoker set search_path = public as $$
declare item jsonb; code text;
begin
 if not public.is_admin() then raise exception 'Only administrators can import samples'; end if;
 if not exists(select 1 from public.projects where id=p_project_id) then raise exception 'Project not found'; end if;
 if exists(select 1 from jsonb_array_elements(p_rows) v where nullif(btrim(v->>'code'),'') is null) then raise exception 'Sample code is required'; end if;
 if exists(select 1 from (select btrim(value->>'code') code from jsonb_array_elements(p_rows)) q group by code having count(*)>1) then raise exception 'Duplicate CSV sample code'; end if;
 if exists(select 1 from jsonb_array_elements(p_rows) v join public.samples s on s.code=btrim(v->>'code')) then raise exception 'Sample code already exists'; end if;
 for item in select value from jsonb_array_elements(p_rows) loop
  insert into public.samples(project_id,code,description,type,status,created_by,updated_by)
  values(p_project_id,btrim(item->>'code'),nullif(btrim(item->>'description'),''),nullif(btrim(item->>'type'),''),'active',auth.uid(),auth.uid());
 end loop;
end $$;

-- A split locks the parent, creates all children, and only rewrites planned associations.
create or replace function public.split_sample(p_sample_id uuid, p_children jsonb) returns void language plpgsql security invoker set search_path = public as $$
declare parent public.samples%rowtype; child jsonb; child_id uuid; code text; n integer; duplicate_count integer;
begin
 if not public.is_admin() then raise exception 'Only administrators can split samples'; end if;
 select * into parent from public.samples where id=p_sample_id for update;
 if not found or parent.status <> 'active' then raise exception 'Only an active sample can be split'; end if;
 n:=jsonb_array_length(p_children); if n < 2 then raise exception 'At least two children are required'; end if;
 select count(*) into duplicate_count from (select btrim(value->>'code') c from jsonb_array_elements(p_children)) q where c='' or c is null;
 if duplicate_count>0 then raise exception 'Child code is required'; end if;
 if exists(select 1 from (select btrim(value->>'code') c from jsonb_array_elements(p_children)) q group by c having count(*)>1) then raise exception 'Duplicate child code'; end if;
 if exists(select 1 from jsonb_array_elements(p_children) j join public.samples s on s.code=btrim(j->>'code')) then raise exception 'Child code already exists'; end if;
 update public.samples set status='split',updated_by=auth.uid() where id=parent.id;
 for child in select value from jsonb_array_elements(p_children) loop
   insert into public.samples(project_id,code,description,type,status,parent_sample_id,created_by,updated_by)
   values(parent.project_id,btrim(child->>'code'),nullif(btrim(child->>'description'),''),nullif(btrim(child->>'type'),''),'active',parent.id,auth.uid(),auth.uid()) returning id into child_id;
 end loop;
 -- Planned associations gain every direct child; historical states remain untouched.
 insert into public.task_samples(task_id,sample_id)
 select ts.task_id, c.id from public.task_samples ts join public.tasks t on t.id=ts.task_id
 join public.samples c on c.parent_sample_id=parent.id and c.status='active'
 where ts.sample_id=parent.id and t.status='planned' on conflict do nothing;
 delete from public.task_samples ts using public.tasks t where ts.task_id=t.id and ts.sample_id=parent.id and t.status='planned';
 perform public.sync_task_zt_count(id) from public.tasks where project_id=parent.project_id;
 insert into public.audit_log(user_id,entity_type,entity_id,action,old_data,new_data) values
 (auth.uid(),'samples',parent.id,'SAMPLE_SPLIT',jsonb_build_object('code',parent.code),p_children);
end $$;
