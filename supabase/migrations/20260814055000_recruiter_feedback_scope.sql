-- Scope recruiter feedback to the real job/candidate pair and restrict writes
-- to owner/recruiter. Viewers remain read-only.
alter table public.recruiter_feedback
  add column if not exists job_title text,
  add column if not exists skills text[] not null default '{}',
  add column if not exists evidence_id text,
  add column if not exists target_skill text,
  add column if not exists polarity text not null default 'positive';

do $$
begin
  alter table public.recruiter_feedback
    add constraint recruiter_feedback_polarity_check
    check (polarity in ('positive', 'negative_calibration'));
exception
  when duplicate_object then null;
end $$;

create or replace function public.recruiter_feedback_belongs_to_workspace(
  p_workspace_id uuid,
  p_screening_job_id uuid,
  p_candidate_profile_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.screening_jobs job
    join public.candidate_profiles candidate
      on candidate.screening_job_id = job.id
    where job.id = p_screening_job_id
      and candidate.id = p_candidate_profile_id
      and job.workspace_id = p_workspace_id
  );
$$;

revoke all on function public.recruiter_feedback_belongs_to_workspace(uuid, uuid, uuid) from public;
grant execute on function public.recruiter_feedback_belongs_to_workspace(uuid, uuid, uuid)
  to authenticated, service_role;

drop policy if exists "members can insert own recruiter feedback" on public.recruiter_feedback;

create policy "owners and recruiters can insert recruiter feedback"
on public.recruiter_feedback for insert
with check (
  public.has_workspace_role(workspace_id, array['owner', 'recruiter']::public.member_role[])
  and created_by = auth.uid()
  and public.recruiter_feedback_belongs_to_workspace(
    workspace_id,
    screening_job_id,
    candidate_profile_id
  )
);
