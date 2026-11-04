-- Split samples remain in lineage history, so their identifying code is immutable.
create or replace function public.prevent_split_sample_code_change()
returns trigger
language plpgsql
as $$
begin
  if old.status = 'split' and new.code is distinct from old.code then
    raise exception 'The code of a split sample cannot be changed';
  end if;
  return new;
end;
$$;

drop trigger if exists samples_prevent_split_code_change on public.samples;
create trigger samples_prevent_split_code_change
before update of code on public.samples
for each row execute function public.prevent_split_sample_code_change();
