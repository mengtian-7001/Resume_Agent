-- Graded trust: Checker pass => model_checked, not human-verified.
DO $$
BEGIN
  ALTER TYPE public.fact_status ADD VALUE 'model_checked';
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

alter table public.agent_memory_chunks
  add column if not exists trust_level text not null default 'untrusted'
    check (trust_level in ('untrusted', 'model_checked', 'source_verified', 'human_verified', 'expired', 'revoked'));

comment on column public.agent_memory_chunks.trust_level is
  'Trust tier. Only human_verified/source_verified should set trusted=true for long-term recall.';

-- Soft recall for model_checked memories (explicit, not mixed into trusted search).
create or replace function public.match_agent_memory_soft(
  target_workspace_id uuid,
  query_embedding vector(256),
  match_count integer default 6
)
returns table (
  id uuid,
  content text,
  metadata jsonb,
  source_revision text,
  trust_level text,
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
    chunk.trust_level,
    1 - (chunk.embedding <=> query_embedding) as similarity
  from public.agent_memory_chunks chunk
  where chunk.workspace_id = target_workspace_id
    and chunk.trust_level = 'model_checked'
    and (chunk.expires_at is null or chunk.expires_at > now())
    and chunk.embedding is not null
  order by chunk.embedding <=> query_embedding
  limit greatest(match_count, 1);
$$;

revoke all on function public.match_agent_memory_soft(uuid, vector, integer) from public;
grant execute on function public.match_agent_memory_soft(uuid, vector, integer) to service_role;
