-- DEV ONLY: explicitly open the local demo workspace for anonymous bootstrap.
-- Never run this against a production project that holds real resumes.

update public.workspaces
set allow_anonymous_bootstrap = true
where id = '93e4a200-ddce-48a4-9386-dbcc9251d590';

-- If the row is missing, create a named demo workspace with the flag on:
insert into public.workspaces (id, name, allow_anonymous_bootstrap)
values ('93e4a200-ddce-48a4-9386-dbcc9251d590', '本地 Demo 工作区', true)
on conflict (id) do update
set allow_anonymous_bootstrap = true,
    name = excluded.name;
