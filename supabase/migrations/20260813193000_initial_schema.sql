create extension if not exists pgcrypto;

create type public.member_role as enum ('owner', 'recruiter', 'viewer');
create type public.screening_status as enum ('draft', 'uploading', 'queued', 'processing', 'completed', 'failed', 'cancelled');
create type public.document_type as enum ('jd', 'resume');
create type public.document_status as enum ('pending', 'validated', 'parsing', 'parsed', 'failed');
create type public.task_type as enum ('validate', 'parse_jd', 'parse_resume', 'match');
create type public.task_status as enum ('queued', 'processing', 'completed', 'failed');
create type public.match_decision as enum ('recommend', 'review', 'reject');

create table public.workspaces (
  id uuid primary key default gen_random_uuid(),
  name text not null check (char_length(trim(name)) between 1 and 120),
  created_at timestamptz not null default now()
);

create table public.workspace_members (
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role public.member_role not null default 'recruiter',
  created_at timestamptz not null default now(),
  primary key (workspace_id, user_id)
);

create table public.screening_jobs (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  title text not null default '未命名筛选任务',
  location text,
  status public.screening_status not null default 'draft',
  created_by uuid not null references auth.users(id),
  candidate_count integer not null default 0 check (candidate_count >= 0),
  processed_count integer not null default 0 check (processed_count >= 0),
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.documents (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  screening_job_id uuid not null references public.screening_jobs(id) on delete cascade,
  document_type public.document_type not null,
  original_filename text not null check (char_length(trim(original_filename)) between 1 and 255),
  storage_path text not null unique,
  mime_type text not null check (mime_type in (
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
  )),
  size_bytes bigint not null check (size_bytes > 0 and size_bytes <= 10485760),
  sha256 text,
  status public.document_status not null default 'pending',
  extracted_text text,
  parse_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (screening_job_id, document_type, original_filename)
);

create unique index documents_one_jd_per_job
  on public.documents(screening_job_id)
  where document_type = 'jd';

create table public.job_requirements (
  id uuid primary key default gen_random_uuid(),
  screening_job_id uuid not null unique references public.screening_jobs(id) on delete cascade,
  source_document_id uuid not null unique references public.documents(id) on delete cascade,
  title text,
  requirements jsonb not null default '{}'::jsonb,
  hard_gates jsonb not null default '[]'::jsonb,
  extracted_at timestamptz not null default now()
);

create table public.candidate_profiles (
  id uuid primary key default gen_random_uuid(),
  screening_job_id uuid not null references public.screening_jobs(id) on delete cascade,
  source_document_id uuid not null unique references public.documents(id) on delete cascade,
  display_name text,
  profile jsonb not null default '{}'::jsonb,
  extracted_at timestamptz not null default now()
);

create table public.match_results (
  id uuid primary key default gen_random_uuid(),
  screening_job_id uuid not null references public.screening_jobs(id) on delete cascade,
  candidate_profile_id uuid not null unique references public.candidate_profiles(id) on delete cascade,
  score numeric(5, 2) not null check (score between 0 and 100),
  decision public.match_decision not null,
  hard_gate_pass boolean not null,
  score_breakdown jsonb not null default '{}'::jsonb,
  evidence jsonb not null default '[]'::jsonb,
  risks jsonb not null default '[]'::jsonb,
  interview_question text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.processing_tasks (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  screening_job_id uuid not null references public.screening_jobs(id) on delete cascade,
  document_id uuid references public.documents(id) on delete cascade,
  dedupe_key text not null unique,
  task_type public.task_type not null,
  status public.task_status not null default 'queued',
  attempts integer not null default 0 check (attempts between 0 and 3),
  available_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  error_message text,
  created_at timestamptz not null default now()
);

create index processing_tasks_queue_idx
  on public.processing_tasks(status, available_at, created_at);
create index screening_jobs_workspace_idx
  on public.screening_jobs(workspace_id, created_at desc);
create index documents_job_idx
  on public.documents(screening_job_id, document_type);
create index match_results_job_idx
  on public.match_results(screening_job_id, score desc);

create table public.audit_logs (
  id bigint generated always as identity primary key,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  actor_id uuid references auth.users(id) on delete set null,
  action text not null,
  resource_type text not null,
  resource_id uuid,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create or replace function public.is_workspace_member(target_workspace_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.workspace_members
    where workspace_id = target_workspace_id
      and user_id = auth.uid()
  );
$$;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create or replace function public.claim_processing_task()
returns setof public.processing_tasks
language plpgsql
security definer
set search_path = public
as $$
declare
  claimed_task public.processing_tasks;
begin
  with next_task as (
    select id
    from public.processing_tasks
    where status = 'queued' and available_at <= now()
    order by created_at
    for update skip locked
    limit 1
  )
  update public.processing_tasks task
  set status = 'processing',
      attempts = attempts + 1,
      started_at = now()
  from next_task
  where task.id = next_task.id
  returning task.* into claimed_task;

  if claimed_task.id is not null then
    return next claimed_task;
  end if;
end;
$$;

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

  if job_workspace is null or not public.is_workspace_member(job_workspace) then
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

create trigger screening_jobs_updated_at
before update on public.screening_jobs
for each row execute procedure public.set_updated_at();

create trigger documents_updated_at
before update on public.documents
for each row execute procedure public.set_updated_at();

create trigger match_results_updated_at
before update on public.match_results
for each row execute procedure public.set_updated_at();

create or replace function public.write_audit_log()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  job_id uuid;
begin
  job_id := case
    when tg_argv[1] = 'screening_job' then new.id
    else new.screening_job_id
  end;
  insert into public.audit_logs (
    workspace_id,
    actor_id,
    action,
    resource_type,
    resource_id,
    metadata
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

create trigger audit_screening_job_created
after insert on public.screening_jobs
for each row execute procedure public.write_audit_log('screening.created', 'screening_job');

create trigger audit_document_uploaded
after insert on public.documents
for each row execute procedure public.write_audit_log('document.uploaded', 'document');

create or replace function public.purge_expired_screenings(retention_days integer default 30)
returns integer
language plpgsql
security definer
set search_path = public, storage
as $$
declare
  deleted_jobs integer;
begin
  if retention_days < 1 then
    raise exception 'retention_days must be positive';
  end if;

  delete from storage.objects
  where bucket_id = 'screening-documents'
    and exists (
      select 1
      from public.documents document
      join public.screening_jobs job on job.id = document.screening_job_id
      where document.storage_path = storage.objects.name
        and job.created_at < now() - make_interval(days => retention_days)
        and job.status in ('completed', 'failed', 'cancelled')
    );

  delete from public.screening_jobs
  where created_at < now() - make_interval(days => retention_days)
    and status in ('completed', 'failed', 'cancelled');
  get diagnostics deleted_jobs = row_count;
  return deleted_jobs;
end;
$$;

alter table public.workspaces enable row level security;
alter table public.workspace_members enable row level security;
alter table public.screening_jobs enable row level security;
alter table public.documents enable row level security;
alter table public.job_requirements enable row level security;
alter table public.candidate_profiles enable row level security;
alter table public.match_results enable row level security;
alter table public.processing_tasks enable row level security;
alter table public.audit_logs enable row level security;

create policy "workspace members can read workspaces"
on public.workspaces for select
using (public.is_workspace_member(id));

create policy "workspace members can read members"
on public.workspace_members for select
using (public.is_workspace_member(workspace_id));

create policy "members can read jobs"
on public.screening_jobs for select
using (public.is_workspace_member(workspace_id));

create policy "recruiters can create jobs"
on public.screening_jobs for insert
with check (
  public.is_workspace_member(workspace_id)
  and created_by = auth.uid()
);

create policy "recruiters can update jobs"
on public.screening_jobs for update
using (public.is_workspace_member(workspace_id))
with check (public.is_workspace_member(workspace_id));

create policy "members can read documents"
on public.documents for select
using (public.is_workspace_member(workspace_id));

create policy "recruiters can create documents"
on public.documents for insert
with check (public.is_workspace_member(workspace_id));

create policy "recruiters can update documents"
on public.documents for update
using (public.is_workspace_member(workspace_id))
with check (public.is_workspace_member(workspace_id));

create policy "members can read job requirements"
on public.job_requirements for select
using (
  exists (
    select 1 from public.screening_jobs j
    where j.id = screening_job_id and public.is_workspace_member(j.workspace_id)
  )
);

create policy "members can read candidate profiles"
on public.candidate_profiles for select
using (
  exists (
    select 1 from public.screening_jobs j
    where j.id = screening_job_id and public.is_workspace_member(j.workspace_id)
  )
);

create policy "members can read match results"
on public.match_results for select
using (
  exists (
    select 1 from public.screening_jobs j
    where j.id = screening_job_id and public.is_workspace_member(j.workspace_id)
  )
);

create policy "members can read processing tasks"
on public.processing_tasks for select
using (public.is_workspace_member(workspace_id));

create policy "members can read audit logs"
on public.audit_logs for select
using (public.is_workspace_member(workspace_id));

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'screening-documents',
  'screening-documents',
  false,
  10485760,
  array[
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
  ]
)
on conflict (id) do update
set public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;
create policy "members can upload scoped files"
on storage.objects for insert
to authenticated
with check (
  bucket_id = 'screening-documents'
  and public.is_workspace_member((storage.foldername(name))[1]::uuid)
);

create policy "members can read scoped files"
on storage.objects for select
to authenticated
using (
  bucket_id = 'screening-documents'
  and public.is_workspace_member((storage.foldername(name))[1]::uuid)
);

create policy "members can delete scoped files"
on storage.objects for delete
to authenticated
using (
  bucket_id = 'screening-documents'
  and public.is_workspace_member((storage.foldername(name))[1]::uuid)
);
