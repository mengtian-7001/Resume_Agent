alter table public.workspaces
  add column if not exists screening_config jsonb not null default '{
    "hard_gates": {
      "min_years": {"enabled": true},
      "education": {"enabled": true},
      "must_have_skills": {"enabled": true, "min_coverage": 1.0}
    },
    "score_thresholds": {
      "recommend_min": 75,
      "review_min": 60
    }
  }'::jsonb;

create policy "members can update workspace screening config"
on public.workspaces for update
using (public.is_workspace_member(id))
with check (public.is_workspace_member(id));
