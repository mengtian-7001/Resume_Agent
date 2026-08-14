-- A legacy trusted=true flag can remain set after the graded-trust migration.
-- Recall only records whose current trust tier is explicitly verified.
create or replace function public.match_agent_memory(
  target_workspace_id uuid,
  query_embedding vector(256),
  match_count integer default 6,
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
    and chunk.trust_level in ('source_verified', 'human_verified')
    and (chunk.expires_at is null or chunk.expires_at > now())
    and (target_memory_type is null or chunk.memory_type = target_memory_type)
    and chunk.embedding is not null
  order by chunk.embedding <=> query_embedding
  limit greatest(match_count, 1);
$$;
