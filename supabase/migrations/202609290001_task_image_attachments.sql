-- Task images use a private Storage bucket; only authenticated users can read
-- them, and only profile-backed administrators can upload or remove them.
create table public.task_attachments (
  id uuid primary key default gen_random_uuid(),
  task_id uuid not null references public.tasks(id) on delete cascade,
  storage_path text not null unique,
  file_name text not null,
  content_type text not null check (content_type in ('image/jpeg', 'image/png')),
  created_at timestamptz not null default now()
);
create index task_attachments_task_id_idx on public.task_attachments(task_id, created_at, id);
alter table public.task_attachments enable row level security;
create policy task_attachments_read on public.task_attachments
  for select to authenticated using (true);
create policy task_attachments_admin_write on public.task_attachments
  for all to authenticated using (public.is_admin()) with check (public.is_admin());

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('task-attachments', 'task-attachments', false, 10485760, array['image/jpeg', 'image/png'])
on conflict (id) do update set public = false, file_size_limit = 10485760,
  allowed_mime_types = array['image/jpeg', 'image/png'];

create policy task_attachment_objects_read on storage.objects
  for select to authenticated using (bucket_id = 'task-attachments');
create policy task_attachment_objects_insert on storage.objects
  for insert to authenticated with check (bucket_id = 'task-attachments' and public.is_admin());
create policy task_attachment_objects_update on storage.objects
  for update to authenticated using (bucket_id = 'task-attachments' and public.is_admin())
  with check (bucket_id = 'task-attachments' and public.is_admin());
create policy task_attachment_objects_delete on storage.objects
  for delete to authenticated using (bucket_id = 'task-attachments' and public.is_admin());
