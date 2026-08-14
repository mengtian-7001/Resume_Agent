"""Persist and log agent-chain steps for live observability."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from supabase import Client

logger = logging.getLogger("agent_chain")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_step(step: dict[str, Any]) -> None:
    logger.info(
        "step=%s status=%s duration_ms=%s model=%s detail=%s",
        step.get("id"),
        step.get("status"),
        step.get("duration_ms"),
        step.get("model") or "-",
        step.get("detail") or "",
    )


def make_step(
    step_id: str,
    label: str,
    *,
    status: str = "running",
    model: str | None = None,
    detail: str | None = None,
    duration_ms: int | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": step_id,
        "label": label,
        "status": status,
        "started_at": started_at or _now(),
    }
    if ended_at:
        step["ended_at"] = ended_at
    if duration_ms is not None:
        step["duration_ms"] = duration_ms
    if model:
        step["model"] = model
    if detail:
        step["detail"] = detail
    if extra:
        step.update(extra)
    return step


class AgentRunTracer:
    """Append-only step stream on agent_runs.state.steps (read-merge-upsert)."""

    def __init__(self, client: Client, *, agent_mode: str = "mock") -> None:
        self.client = client
        self.agent_mode = agent_mode

    def ensure_run(self, workspace_id: str, screening_job_id: str, *, status: str = "running") -> None:
        existing = self._fetch(screening_job_id)
        state = dict((existing or {}).get("state") or {})
        state.setdefault("steps", [])
        state.setdefault("stage", "running")
        payload: dict[str, Any] = {
            "workspace_id": workspace_id,
            "screening_job_id": screening_job_id,
            "status": status,
            "mode": self.agent_mode,
            "state": state,
        }
        if not existing or not existing.get("started_at"):
            payload["started_at"] = _now()
        if status == "completed":
            payload["completed_at"] = _now()
        self.client.table("agent_runs").upsert(payload, on_conflict="screening_job_id").execute()

    def append_step(
        self,
        workspace_id: str,
        screening_job_id: str,
        step: dict[str, Any],
        *,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        merge_state: Optional[dict[str, Any]] = None,
    ) -> None:
        log_step(step)
        existing = self._fetch(screening_job_id)
        state = dict((existing or {}).get("state") or {})
        steps = list(state.get("steps") or [])
        # Update in-place if same id is still running; else append.
        # Parallel candidates can share a logical step id unless the worker
        # suffixes candidate_id, so never merge across different people.
        updated = False
        step_cid = step.get("candidate_id")
        for index, prior in enumerate(steps):
            if prior.get("id") != step.get("id") or prior.get("status") != "running":
                continue
            prior_cid = prior.get("candidate_id")
            if prior_cid and step_cid and prior_cid != step_cid:
                continue
            steps[index] = {**prior, **step}
            updated = True
            break
        if not updated:
            steps.append(step)
        state["steps"] = steps
        if stage:
            state["stage"] = stage
        if merge_state:
            state.update(merge_state)
        payload: dict[str, Any] = {
            "workspace_id": workspace_id,
            "screening_job_id": screening_job_id,
            "status": status or (existing or {}).get("status") or "running",
            "mode": self.agent_mode,
            "state": state,
        }
        if not existing or not existing.get("started_at"):
            payload["started_at"] = _now()
        if payload["status"] == "completed":
            payload["completed_at"] = _now()
        self.client.table("agent_runs").upsert(payload, on_conflict="screening_job_id").execute()

    def complete(
        self,
        workspace_id: str,
        screening_job_id: str,
        *,
        merge_state: Optional[dict[str, Any]] = None,
    ) -> None:
        existing = self._fetch(screening_job_id)
        state = dict((existing or {}).get("state") or {})
        state["stage"] = "completed"
        state.pop("failure_reason", None)
        if merge_state:
            state.update(merge_state)
        self.client.table("agent_runs").upsert(
            {
                "workspace_id": workspace_id,
                "screening_job_id": screening_job_id,
                "status": "completed",
                "mode": self.agent_mode,
                "state": state,
                "completed_at": _now(),
                "started_at": (existing or {}).get("started_at") or _now(),
            },
            on_conflict="screening_job_id",
        ).execute()

    def fail(
        self,
        workspace_id: str,
        screening_job_id: str,
        *,
        reason: str | None = None,
        merge_state: Optional[dict[str, Any]] = None,
    ) -> None:
        """Mark the agent run failed. Never writes status/stage=completed."""
        existing = self._fetch(screening_job_id)
        state = dict((existing or {}).get("state") or {})
        state["stage"] = "failed"
        state["failed"] = True
        if reason:
            state["failure_reason"] = reason[:500]
        if merge_state:
            state.update(merge_state)
        self.client.table("agent_runs").upsert(
            {
                "workspace_id": workspace_id,
                "screening_job_id": screening_job_id,
                "status": "failed",
                "mode": self.agent_mode,
                "state": state,
                "completed_at": _now(),
                "started_at": (existing or {}).get("started_at") or _now(),
            },
            on_conflict="screening_job_id",
        ).execute()

    def _fetch(self, screening_job_id: str) -> dict[str, Any] | None:
        try:
            rows = (
                self.client.table("agent_runs")
                .select("status,state,started_at,completed_at")
                .eq("screening_job_id", screening_job_id)
                .limit(1)
                .execute()
                .data
            ) or []
            return rows[0] if rows else None
        except Exception:
            return None
