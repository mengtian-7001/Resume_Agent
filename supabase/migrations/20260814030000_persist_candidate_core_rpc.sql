-- Atomic core persist for one candidate: match_results + question_packs + checker_reviews
-- (+ optional fact_claims replace). Service-role only.

create or replace function public.persist_screening_candidate_core(
  p_workspace_id uuid,
  p_screening_job_id uuid,
  p_candidate_profile_id uuid,
  p_match jsonb,
  p_questions jsonb,
  p_followups jsonb,
  p_review jsonb,
  p_claims jsonb default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  review_status text;
  mapped_status public.review_status;
  claim_count integer := 0;
begin
  if p_workspace_id is null or p_screening_job_id is null or p_candidate_profile_id is null then
    raise exception 'workspace_id, screening_job_id and candidate_profile_id are required';
  end if;

  review_status := lower(coalesce(p_review->>'status', 'fail'));
  if review_status in ('pass') then
    mapped_status := 'pass';
  else
    -- degraded / fail / anything else → fail (fail-closed)
    mapped_status := 'fail';
  end if;

  insert into public.match_results (
    screening_job_id,
    candidate_profile_id,
    score,
    decision,
    hard_gate_pass,
    score_breakdown,
    evidence,
    risks,
    interview_question
  ) values (
    p_screening_job_id,
    p_candidate_profile_id,
    coalesce((p_match->>'score')::numeric, 0),
    coalesce(p_match->>'decision', 'review'),
    coalesce((p_match->>'hard_gate_pass')::boolean, false),
    coalesce(p_match->'score_breakdown', '{}'::jsonb),
    coalesce(p_match->'evidence', '[]'::jsonb),
    coalesce(p_match->'risks', '[]'::jsonb),
    nullif(p_match->>'interview_question', '')
  )
  on conflict (candidate_profile_id) do update set
    screening_job_id = excluded.screening_job_id,
    score = excluded.score,
    decision = excluded.decision,
    hard_gate_pass = excluded.hard_gate_pass,
    score_breakdown = excluded.score_breakdown,
    evidence = excluded.evidence,
    risks = excluded.risks,
    interview_question = excluded.interview_question,
    updated_at = now();

  insert into public.question_packs (
    workspace_id,
    screening_job_id,
    candidate_profile_id,
    questions,
    followups,
    quality
  ) values (
    p_workspace_id,
    p_screening_job_id,
    p_candidate_profile_id,
    coalesce(p_questions, '[]'::jsonb),
    coalesce(p_followups, '[]'::jsonb),
    jsonb_build_object(
      'question_count', jsonb_array_length(coalesce(p_questions, '[]'::jsonb)),
      'followup_count', jsonb_array_length(coalesce(p_followups, '[]'::jsonb)),
      'checker_status', review_status
    )
  )
  on conflict (candidate_profile_id) do update set
    workspace_id = excluded.workspace_id,
    screening_job_id = excluded.screening_job_id,
    questions = excluded.questions,
    followups = excluded.followups,
    quality = excluded.quality,
    updated_at = now();

  insert into public.checker_reviews (
    workspace_id,
    screening_job_id,
    candidate_profile_id,
    status,
    feedback,
    model,
    reviewed_at
  ) values (
    p_workspace_id,
    p_screening_job_id,
    p_candidate_profile_id,
    mapped_status,
    coalesce(p_review->'issues', p_review->'feedback', '[]'::jsonb),
    coalesce(nullif(p_review->>'model', ''), 'unknown'),
    now()
  )
  on conflict (candidate_profile_id) do update set
    workspace_id = excluded.workspace_id,
    screening_job_id = excluded.screening_job_id,
    status = excluded.status,
    feedback = excluded.feedback,
    model = excluded.model,
    reviewed_at = now();

  if p_claims is not null and jsonb_typeof(p_claims) = 'array' then
    delete from public.fact_claims
    where candidate_profile_id = p_candidate_profile_id;

    insert into public.fact_claims (
      workspace_id,
      screening_job_id,
      candidate_profile_id,
      subject_type,
      predicate,
      value,
      normalized_value,
      confidence,
      status,
      evidence,
      producer,
      producer_version
    )
    select
      p_workspace_id,
      p_screening_job_id,
      p_candidate_profile_id,
      coalesce(c->>'subject_type', 'candidate'),
      coalesce(c->>'predicate', 'unknown'),
      jsonb_build_object('value', c->'value'),
      lower(coalesce(c->>'normalized_value', c->>'value', '')),
      coalesce(nullif(c->>'confidence', ''), 'medium'),
      case
        when coalesce(c->>'status', '') = 'model_checked' then 'model_checked'::public.fact_status
        when coalesce(c->>'status', '') = 'verified' then 'verified'::public.fact_status
        else 'proposed'::public.fact_status
      end,
      coalesce(c->'evidence', '[]'::jsonb),
      coalesce(nullif(c->>'producer', ''), 'construction'),
      c->>'producer_version'
    from jsonb_array_elements(p_claims) as c;

    get diagnostics claim_count = row_count;
  end if;

  return jsonb_build_object(
    'ok', true,
    'candidate_profile_id', p_candidate_profile_id,
    'claims_written', claim_count,
    'review_status', mapped_status::text
  );
end;
$$;

revoke all on function public.persist_screening_candidate_core(
  uuid, uuid, uuid, jsonb, jsonb, jsonb, jsonb, jsonb
) from public;

grant execute on function public.persist_screening_candidate_core(
  uuid, uuid, uuid, jsonb, jsonb, jsonb, jsonb, jsonb
) to service_role;
