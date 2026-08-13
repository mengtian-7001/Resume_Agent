-- Lets an authenticated anonymous session join only this pre-provisioned
-- local workspace as a recruiter. The workspace ID is intentionally kept
-- server-side, so callers cannot choose another workspace.
create or replace function public.bootstrap_anonymous_workspace()
returns public.member_role
language plpgsql
security definer
set search_path = public
as $$
declare
  allowed_workspace_id constant uuid := '93e4a200-ddce-48a4-9386-dbcc9251d590';
  membership_role public.member_role;
begin
  if auth.uid() is null then
    raise exception 'authentication required';
  end if;

  if not exists (
    select 1
    from public.workspaces
    where id = allowed_workspace_id
  ) then
    raise exception 'workspace not found';
  end if;

  insert into public.workspace_members (workspace_id, user_id, role)
  values (allowed_workspace_id, auth.uid(), 'recruiter')
  on conflict (workspace_id, user_id) do nothing;

  select role
  into membership_role
  from public.workspace_members
  where workspace_id = allowed_workspace_id
    and user_id = auth.uid();

  return membership_role;
end;
$$;

revoke all on function public.bootstrap_anonymous_workspace() from public;
grant execute on function public.bootstrap_anonymous_workspace() to authenticated;

-- Use the actual trigger table instead of an argument label, so job creation
-- always audits its own ID rather than trying to read a documents-only column.
create or replace function public.write_audit_log()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  job_id uuid;
begin
  job_id := coalesce(
    nullif(to_jsonb(new) ->> 'screening_job_id', '')::uuid,
    new.id
  );

  insert into public.audit_logs (
    workspace_id, actor_id, action, resource_type, resource_id, metadata
  ) values (
    new.workspace_id,
    auth.uid(),
    tg_argv[0],
    tg_argv[1],
    new.id,
    jsonb_build_object('screening_job_id', job_id)
  );

  return new;
end;
$$;
