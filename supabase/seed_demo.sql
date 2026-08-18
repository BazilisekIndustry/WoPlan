-- Optional demo data. Run only after migrations. It is idempotent and clearly labelled DEMO.
insert into public.workplaces (name, description, hours_per_workday, working_days, annual_capacity_hours)
values
  ('Chamber 1', 'DEMO – standard 8h chamber', 8, array[0,1,2,3,4]::smallint[], 2080),
  ('Chamber 2', 'DEMO – standard 8h chamber', 8, array[0,1,2,3,4]::smallint[], 2080),
  ('Chamber 3', 'DEMO – 24h chamber', 24, array[0,1,2,3,4,5,6]::smallint[], 8760),
  ('CNC', 'DEMO – standard 8h workplace', 8, array[0,1,2,3,4]::smallint[], 2080),
  ('Metrology', 'DEMO – standard 8h workplace', 8, array[0,1,2,3,4]::smallint[], 2080),
  ('Laboratory', 'DEMO – standard 8h workplace', 8, array[0,1,2,3,4]::smallint[], 2080)
on conflict (name) do nothing;

insert into public.projects (project_number, name, description, planned_end)
values
  ('DEMO-100', 'Dependency chain', 'DEMO – chain, shift it in the application to see propagation.', '2026-09-15'),
  ('DEMO-200', 'Chamber conflict', 'DEMO – overlaps with DEMO-100 on Chamber 2.', '2026-09-01'),
  ('DEMO-300', '24 hour operation', 'DEMO – continuous 24-hour workplace.', '2026-10-01')
on conflict (project_number) do nothing;

insert into public.tasks (project_id, name, workplace_id, duration_workdays, zt_count, planned_start, planned_end, status)
select p.id, x.name, w.id, x.duration, x.zt, x.start_date, x.end_date, x.status::public.task_status
from (values
  ('DEMO-100', 'Irradiation', 'Chamber 1', 5, 12, date '2026-08-17', date '2026-08-21', 'planned'),
  ('DEMO-100', 'Inspection', 'Chamber 2', 5, 12, date '2026-08-26', date '2026-09-01', 'planned'),
  ('DEMO-100', 'Evaluation', 'Metrology', 2, 12, date '2026-09-04', date '2026-09-07', 'planned'),
  ('DEMO-200', 'External chamber run', 'Chamber 2', 5, 8, date '2026-08-28', date '2026-09-03', 'planned'),
  ('DEMO-300', 'Continuous exposure', 'Chamber 3', 10, 20, date '2026-09-01', date '2026-09-10', 'planned')
) as x(project_number, name, workplace_name, duration, zt, start_date, end_date, status)
join public.projects p on p.project_number = x.project_number
join public.workplaces w on w.name = x.workplace_name
where not exists (select 1 from public.tasks t where t.project_id = p.id and t.name = x.name);

insert into public.task_dependencies (predecessor_task_id, successor_task_id, offset_workdays)
select a.id, b.id, 3 from public.tasks a join public.tasks b on true
join public.projects pa on pa.id = a.project_id join public.projects pb on pb.id = b.project_id
where pa.project_number = 'DEMO-100' and pb.project_number = 'DEMO-100'
  and (a.name, b.name) in (('Irradiation', 'Inspection'), ('Inspection', 'Evaluation'))
on conflict (predecessor_task_id, successor_task_id) do nothing;
