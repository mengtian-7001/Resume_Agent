-- Keep a single product default across DB, backend and frontend.
-- Existing workspace-specific configurations are intentionally preserved.

alter table public.workspaces
  add column if not exists screening_config jsonb not null default '{
    "hard_gates": {
      "min_years": {"enabled": true},
      "education": {"enabled": true},
      "must_have_skills": {"enabled": true, "min_coverage": 0.5}
    },
    "score_thresholds": {
      "recommend_min": 75,
      "review_min": 60
    }
  }'::jsonb;
