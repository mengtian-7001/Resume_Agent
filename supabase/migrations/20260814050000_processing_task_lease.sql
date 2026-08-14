-- Task lease: reclaim stuck processing tasks after worker crash / platform timeout.

alter table public.processing_tasks
  add column if not exists lease_expires_at timestamptz;

comment on column public.processing_tasks.lease_expires_at is
  'When status=processing, task may be reclaimed after this timestamp. Null for queued/completed/failed.';

create index if not exists processing_tasks_lease_idx
  on public.processing_tasks (status, lease_expires_at)
  where status = 'processing';

create or replace function public.claim_processing_task(
  p_lease_seconds integer default 180
)
returns setof public.processing_tasks
language plpgsql
security definer
set search_path = public
as $$
declare
  claimed_task public.processing_tasks;
  lease_secs integer := greatest(30, least(coalesce(p_lease_seconds, 180), 600));
begin
  -- Reclaim expired leases before picking the next task.
  update public.processing_tasks
  set status = 'queued',
      lease_expires_at = null,
      available_at = now(),
      error_message = coalesce(nullif(error_message, ''), 'lease_expired_requeued')
  where status = 'processing'
    and attempts < 3
    and (
      (lease_expires_at is not null and lease_expires_at < now())
      or (
        lease_expires_at is null
        and started_at is not null
        and started_at < now() - interval '10 minutes'
      )
    );

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
      started_at = now(),
      lease_expires_at = now() + make_interval(secs => lease_secs)
  from next_task
  where task.id = next_task.id
  returning task.* into claimed_task;

  if claimed_task.id is not null then
    return next claimed_task;
  end if;
end;
$$;

-- Keep zero-arg overload for existing workers / PostgREST.
create or replace function public.claim_processing_task()
returns setof public.processing_tasks
language sql
security definer
set search_path = public
as $$
  select * from public.claim_processing_task(180);
$$;

revoke all on function public.claim_processing_task() from public;
revoke all on function public.claim_processing_task() from anon, authenticated;
grant execute on function public.claim_processing_task() to service_role;

revoke all on function public.claim_processing_task(integer) from public;
revoke all on function public.claim_processing_task(integer) from anon, authenticated;
grant execute on function public.claim_processing_task(integer) to service_role;

create or replace function public.heartbeat_processing_task(
  p_task_id uuid,
  p_lease_seconds integer default 180
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  lease_secs integer := greatest(30, least(coalesce(p_lease_seconds, 180), 600));
begin
  update public.processing_tasks
  set lease_expires_at = now() + make_interval(secs => lease_secs)
  where id = p_task_id
    and status = 'processing';
end;
$$;

revoke all on function public.heartbeat_processing_task(uuid, integer) from public;
revoke all on function public.heartbeat_processing_task(uuid, integer) from anon, authenticated;
grant execute on function public.heartbeat_processing_task(uuid, integer) to service_role;
