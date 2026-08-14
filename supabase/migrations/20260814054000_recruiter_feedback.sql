-- Recruiter feedback flywheel. Human confirmation can promote later recall
-- to human_verified; model_checked memories still cannot raise scores.
create table if not exists public.recruiter_feedback (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  screening_job_id uuid not null references public.screening_jobs(id) on delete cascade,
  candidate_profile_id uuid not null references public.candidate_profiles(id) on delete cascade,
  feedback_type text not null check (feedback_type in ('decision', 'question', 'candidate_status', 'evidence')),
  value text not null,
  comment text,
  created_by uuid not null references auth.users(id),
  created_at timestamptz not null default now()
);

create index if not exists recruiter_feedback_candidate_idx
  on public.recruiter_feedback (workspace_id, candidate_profile_id, created_at desc);

comment on table public.recruiter_feedback is
  'Recruiter judgements used to promote memory trust. Only human_verified/source_verified may affect later scoring.';

alter table public.recruiter_feedback enable row level security;

create policy "members can read recruiter feedback"
on public.recruiter_feedback for select
using (public.is_workspace_member(workspace_id));

create policy "members can insert own recruiter feedback"
on public.recruiter_feedback for insert
with check (
  public.is_workspace_member(workspace_id)
  and created_by = auth.uid()
);

grant select, insert on public.recruiter_feedback to authenticated;
grant all on public.recruiter_feedback to service_role;
