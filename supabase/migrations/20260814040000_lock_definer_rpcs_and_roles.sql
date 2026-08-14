-- P0/P1: lock SECURITY DEFINER RPCs to service_role; enforce recruiter/owner writes.
-- Also turn off any auto-enabled anonymous bootstrap left by prior migrations.

-- ─── Definer RPC grants ─────────────────────────────────────────────────────
revoke all on function public.claim_processing_task() from public;
revoke all on function public.claim_processing_task() from anon, authenticated;
grant execute on function public.claim_processing_task() to service_role;

revoke all on function public.purge_expired_screenings(integer) from public;
revoke all on function public.purge_expired_screenings(integer) from anon, authenticated;
grant execute on function public.purge_expired_screenings(integer) to service_role;

-- start_screening stays callable by authenticated members, but require recruiter/owner.
create or replace function public.has_workspace_role(
  target_workspace_id uuid,
  allowed_roles public.member_role[]
)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.workspace_members
    where workspace_id = target_workspace_id
      and user_id = auth.uid()
      and role = any (allowed_roles)
  );
$$;

revoke all on function public.has_workspace_role(uuid, public.member_role[]) from public;
grant execute on function public.has_workspace_role(uuid, public.member_role[]) to authenticated, service_role;

create or replace function public.start_screening(target_job_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  job_workspace uuid;
  jd_id uuid;
  resume_total integer;
begin
  select workspace_id into job_workspace
  from public.screening_jobs
  where id = target_job_id;

  if job_workspace is null
     or not public.has_workspace_role(job_workspace, array['owner', 'recruiter']::public.member_role[]) then
    raise exception 'not authorized';
  end if;

  select id into jd_id
  from public.documents
  where screening_job_id = target_job_id and document_type = 'jd' and status in ('pending', 'validated')
  limit 1;

  select count(*) into resume_total
  from public.documents
  where screening_job_id = target_job_id and document_type = 'resume' and status in ('pending', 'validated');

  if jd_id is null then
    raise exception 'exactly one valid JD is required';
  end if;
  if resume_total < 1 then
    raise exception 'at least one valid resume is required';
  end if;

  update public.screening_jobs
  set status = 'queued', candidate_count = resume_total, processed_count = 0, error_message = null
  where id = target_job_id;

  insert into public.processing_tasks (
    workspace_id, screening_job_id, document_id, dedupe_key, task_type
  ) values (
    job_workspace, target_job_id, jd_id, 'parse_jd:' || jd_id::text, 'parse_jd'
  ) on conflict (dedupe_key) do nothing;
end;
$$;

revoke all on function public.start_screening(uuid) from public;
grant execute on function public.start_screening(uuid) to authenticated, service_role;

-- ─── RLS: recruiters/owners write; viewers read-only ────────────────────────
drop policy if exists "recruiters can create jobs" on public.screening_jobs;
create policy "recruiters can create jobs"
on public.screening_jobs for insert
with check (
  public.has_workspace_role(workspace_id, array['owner', 'recruiter']::public.member_role[])
  and created_by = auth.uid()
);

drop policy if exists "recruiters can update jobs" on public.screening_jobs;
create policy "recruiters can update jobs"
on public.screening_jobs for update
using (public.has_workspace_role(workspace_id, array['owner', 'recruiter']::public.member_role[]))
with check (public.has_workspace_role(workspace_id, array['owner', 'recruiter']::public.member_role[]));

drop policy if exists "recruiters can create documents" on public.documents;
create policy "recruiters can create documents"
on public.documents for insert
with check (
  public.has_workspace_role(workspace_id, array['owner', 'recruiter']::public.member_role[])
);

drop policy if exists "recruiters can update documents" on public.documents;
create policy "recruiters can update documents"
on public.documents for update
using (public.has_workspace_role(workspace_id, array['owner', 'recruiter']::public.member_role[]))
with check (public.has_workspace_role(workspace_id, array['owner', 'recruiter']::public.member_role[]));

drop policy if exists "members can update workspace screening config" on public.workspaces;
create policy "owners and recruiters can update workspace screening config"
on public.workspaces for update
using (public.has_workspace_role(id, array['owner', 'recruiter']::public.member_role[]))
with check (public.has_workspace_role(id, array['owner', 'recruiter']::public.member_role[]));

drop policy if exists "members can upload scoped files" on storage.objects;
create policy "recruiters can upload scoped files"
on storage.objects for insert
to authenticated
with check (
  bucket_id = 'screening-documents'
  and public.has_workspace_role(
    (storage.foldername(name))[1]::uuid,
    array['owner', 'recruiter']::public.member_role[]
  )
);

drop policy if exists "members can delete scoped files" on storage.objects;
create policy "recruiters can delete scoped files"
on storage.objects for delete
to authenticated
using (
  bucket_id = 'screening-documents'
  and public.has_workspace_role(
    (storage.foldername(name))[1]::uuid,
    array['owner', 'recruiter']::public.member_role[]
  )
);

-- Do not leave demo workspace anonymously open after schema migrations.
-- Column may be absent if gate_anonymous_bootstrap migration was not applied yet.
do $$
begin
  if exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'workspaces'
      and column_name = 'allow_anonymous_bootstrap'
  ) then
    update public.workspaces
    set allow_anonymous_bootstrap = false
    where id = '93e4a200-ddce-48a4-9386-dbcc9251d590'
      and allow_anonymous_bootstrap is true;
  end if;
end $$;
