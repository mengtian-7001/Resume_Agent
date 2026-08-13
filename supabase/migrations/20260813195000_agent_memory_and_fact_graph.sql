create extension if not exists vector;

create type public.agent_run_status as enum ('queued', 'running', 'reviewing', 'completed', 'failed');
create type public.fact_status as enum ('proposed', 'verified', 'challenged', 'retracted', 'superseded');
create type public.review_status as enum ('pass', 'fail');

create table public.agent_runs (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  screening_job_id uuid not null unique references public.screening_jobs(id) on delete cascade,
  status public.agent_run_status not null default 'queued',
  mode text not null default 'mock',
  state jsonb not null default '{}'::jsonb,
  state_revision integer not null default 1 check (state_revision > 0),
  error_message text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.fact_claims (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  screening_job_id uuid not null references public.screening_jobs(id) on delete cascade,
  candidate_profile_id uuid references public.candidate_profiles(id) on delete cascade,
  subject_type text not null,
  predicate text not null,
  value jsonb not null,
  normalized_value text,
  confidence text not null default 'medium' check (confidence in ('low', 'medium', 'high')),
  status public.fact_status not null default 'proposed',
  evidence jsonb not null default '[]'::jsonb,
  producer text not null,
  producer_version text,
  revision integer not null default 1 check (revision > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index fact_claims_job_candidate_idx
  on public.fact_claims(screening_job_id, candidate_profile_id, predicate, status);

create table public.question_packs (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  screening_job_id uuid not null references public.screening_jobs(id) on delete cascade,
  candidate_profile_id uuid not null unique references public.candidate_profiles(id) on delete cascade,
  questions jsonb not null default '[]'::jsonb,
  followups jsonb not null default '[]'::jsonb,
  quality jsonb not null default '{}'::jsonb,
  source_claim_ids jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.checker_reviews (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  screening_job_id uuid not null references public.screening_jobs(id) on delete cascade,
  candidate_profile_id uuid not null unique references public.candidate_profiles(id) on delete cascade,
  status public.review_status not null,
  feedback jsonb not null default '[]'::jsonb,
  model text not null,
  retry_count integer not null default 0 check (retry_count >= 0),
  reviewed_at timestamptz not null default now()
);

create table public.agent_memory_chunks (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  screening_job_id uuid references public.screening_jobs(id) on delete cascade,
  memory_type text not null check (memory_type in ('run', 'job', 'question', 'review')),
  content text not null,
  metadata jsonb not null default '{}'::jsonb,
  source_revision text not null,
  trusted boolean not null default false,
  expires_at timestamptz,
  embedding vector(256),
  created_at timestamptz not null default now()
);

create index agent_memory_chunks_scope_idx
  on public.agent_memory_chunks(workspace_id, memory_type, trusted, created_at desc);
create index agent_memory_chunks_embedding_idx
  on public.agent_memory_chunks using ivfflat (embedding vector_cosine_ops) with (lists = 100);

create or replace function public.match_agent_memory(
  target_workspace_id uuid,
  query_embedding vector(256),
  match_count integer default 8,
  target_memory_type text default null
)
returns table (
  id uuid,
  content text,
  metadata jsonb,
  source_revision text,
  similarity real
)
language sql
stable
as $$
  select
    chunk.id,
    chunk.content,
    chunk.metadata,
    chunk.source_revision,
    1 - (chunk.embedding <=> query_embedding) as similarity
  from public.agent_memory_chunks chunk
  where chunk.workspace_id = target_workspace_id
    and chunk.trusted = true
    and (chunk.expires_at is null or chunk.expires_at > now())
    and (target_memory_type is null or chunk.memory_type = target_memory_type)
    and chunk.embedding is not null
  order by chunk.embedding <=> query_embedding
  limit greatest(match_count, 1);
$$;

create trigger agent_runs_updated_at
before update on public.agent_runs
for each row execute procedure public.set_updated_at();

create trigger fact_claims_updated_at
before update on public.fact_claims
for each row execute procedure public.set_updated_at();

create trigger question_packs_updated_at
before update on public.question_packs
for each row execute procedure public.set_updated_at();

alter table public.agent_runs enable row level security;
alter table public.fact_claims enable row level security;
alter table public.question_packs enable row level security;
alter table public.checker_reviews enable row level security;
alter table public.agent_memory_chunks enable row level security;

create policy "members can read agent runs"
on public.agent_runs for select
using (public.is_workspace_member(workspace_id));

create policy "members can read fact claims"
on public.fact_claims for select
using (public.is_workspace_member(workspace_id));

create policy "members can read question packs"
on public.question_packs for select
using (public.is_workspace_member(workspace_id));

create policy "members can read checker reviews"
on public.checker_reviews for select
using (public.is_workspace_member(workspace_id));

create policy "members can read agent memory"
on public.agent_memory_chunks for select
using (public.is_workspace_member(workspace_id));
