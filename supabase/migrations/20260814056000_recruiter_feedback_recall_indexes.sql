-- Candidate-scoped recall and job-level question templates should hit indexes
-- instead of a workspace-wide recency window.
create index if not exists recruiter_feedback_job_type_idx
  on public.recruiter_feedback (workspace_id, screening_job_id, feedback_type, created_at desc);

create index if not exists recruiter_feedback_question_title_idx
  on public.recruiter_feedback (workspace_id, feedback_type, created_at desc)
  where feedback_type = 'question';

comment on table public.recruiter_feedback is
  'Recruiter judgements. Only evidence=confirmed may become human_verified for scoring; decision/status/question stay calibration, outcome, or question_pattern.';
