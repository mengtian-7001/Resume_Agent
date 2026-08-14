-- Lease ownership prevents a worker that lost its lease from completing or
-- failing a task after another worker reclaimed it.

alter table public.processing_tasks
  add column if not exists lease_token uuid;

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
  update public.processing_tasks
  set status = case when attempts < 3 then 'queued'::public.task_status else 'failed'::public.task_status end,
      lease_expires_at = null,
      lease_token = null,
      available_at = now(),
      completed_at = case when attempts < 3 then null else now() end,
      error_message = coalesce(nullif(error_message, ''), 'lease_expired_requeued')
  where status = 'processing'
    and (
      (lease_expires_at is not null and lease_expires_at < now())
      or (lease_expires_at is null and started_at < now() - interval '10 minutes')
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
      lease_expires_at = now() + make_interval(secs => lease_secs),
      lease_token = gen_random_uuid()
  from next_task
  where task.id = next_task.id
  returning task.* into claimed_task;

  if claimed_task.id is not null then
    return next claimed_task;
  end if;
end;
$$;

create or replace function public.claim_processing_task_for_job(
  p_job_id uuid,
  p_lease_seconds integer default 180,
  p_task_type public.task_type default null
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
  update public.processing_tasks
  set status = case when attempts < 3 then 'queued'::public.task_status else 'failed'::public.task_status end,
      lease_expires_at = null,
      lease_token = null,
      available_at = now(),
      completed_at = case when attempts < 3 then null else now() end,
      error_message = coalesce(nullif(error_message, ''), 'lease_expired_requeued')
  where screening_job_id = p_job_id
    and status = 'processing'
    and (
      (lease_expires_at is not null and lease_expires_at < now())
      or (lease_expires_at is null and started_at < now() - interval '10 minutes')
    );

  with next_task as (
    select id
    from public.processing_tasks
    where screening_job_id = p_job_id
      and status = 'queued'
      and available_at <= now()
      and (p_task_type is null or task_type = p_task_type)
    order by
      case task_type when 'parse_jd' then 0 when 'parse_resume' then 1 else 2 end,
      created_at
    for update skip locked
    limit 1
  )
  update public.processing_tasks task
  set status = 'processing',
      attempts = attempts + 1,
      started_at = now(),
      lease_expires_at = now() + make_interval(secs => lease_secs),
      lease_token = gen_random_uuid()
  from next_task
  where task.id = next_task.id
  returning task.* into claimed_task;

  if claimed_task.id is not null then
    return next claimed_task;
  end if;
end;
$$;

create or replace function public.heartbeat_processing_task(
  p_task_id uuid,
  p_lease_seconds integer,
  p_lease_token uuid
)
returns boolean
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
    and status = 'processing'
    and lease_token = p_lease_token;
  return found;
end;
$$;

create or replace function public.complete_processing_task(
  p_task_id uuid,
  p_lease_token uuid
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.processing_tasks
  set status = 'completed',
      completed_at = now(),
      lease_expires_at = null,
      lease_token = null
  where id = p_task_id
    and status = 'processing'
    and lease_token = p_lease_token;
  return found;
end;
$$;

create or replace function public.fail_processing_task(
  p_task_id uuid,
  p_lease_token uuid,
  p_error_message text
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.processing_tasks
  set status = case when attempts < 3 then 'queued'::public.task_status else 'failed'::public.task_status end,
      available_at = now(),
      completed_at = case when attempts < 3 then null else now() end,
      error_message = left(coalesce(p_error_message, 'task failed'), 1000),
      lease_expires_at = null,
      lease_token = null
  where id = p_task_id
    and status = 'processing'
    and lease_token = p_lease_token;
  return found;
end;
$$;

revoke all on function public.claim_processing_task(integer) from public, anon, authenticated;
grant execute on function public.claim_processing_task(integer) to service_role;
revoke all on function public.claim_processing_task_for_job(uuid, integer, public.task_type) from public, anon, authenticated;
grant execute on function public.claim_processing_task_for_job(uuid, integer, public.task_type) to service_role;
revoke all on function public.heartbeat_processing_task(uuid, integer, uuid) from public, anon, authenticated;
grant execute on function public.heartbeat_processing_task(uuid, integer, uuid) to service_role;
revoke all on function public.complete_processing_task(uuid, uuid) from public, anon, authenticated;
grant execute on function public.complete_processing_task(uuid, uuid) to service_role;
revoke all on function public.fail_processing_task(uuid, uuid, text) from public, anon, authenticated;
grant execute on function public.fail_processing_task(uuid, uuid, text) to service_role;
