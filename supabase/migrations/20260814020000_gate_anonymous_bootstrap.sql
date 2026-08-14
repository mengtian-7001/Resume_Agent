-- DEV ONLY: anonymous bootstrap must be explicitly enabled per workspace.
-- Production workspaces leave allow_anonymous_bootstrap unset/false.
alter table public.workspaces
  add column if not exists allow_anonymous_bootstrap boolean not null default false;

comment on column public.workspaces.allow_anonymous_bootstrap is
  'DEV ONLY. When true, authenticated (including anonymous) users may self-join this workspace as recruiter via bootstrap_anonymous_workspace(). Never enable on production workspaces that hold real resumes.';

create or replace function public.bootstrap_anonymous_workspace()
returns public.member_role
language plpgsql
security definer
set search_path = public
as $$
declare
  allowed_workspace_id constant uuid := '93e4a200-ddce-48a4-9386-dbcc9251d590';
  membership_role public.member_role;
  bootstrap_allowed boolean;
begin
  if auth.uid() is null then
    raise exception 'authentication required';
  end if;

  select coalesce(allow_anonymous_bootstrap, false)
  into bootstrap_allowed
  from public.workspaces
  where id = allowed_workspace_id;

  if bootstrap_allowed is null then
    raise exception 'workspace not found';
  end if;

  if not bootstrap_allowed then
    raise exception 'anonymous workspace bootstrap is disabled (dev-only flag)';
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

-- Intentionally do NOT auto-enable allow_anonymous_bootstrap here.
-- Local demo opt-in: scripts/enable-demo-anonymous.sql (or set the flag manually).
