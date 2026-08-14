"""Task claim/lease RPC adapter, isolated from screening orchestration."""

from __future__ import annotations

from typing import Any


class TaskLeaseClient:
    def __init__(self, client: Any, lease_seconds: int) -> None:
        self.client = client
        self.lease_seconds = max(30, min(int(lease_seconds or 300), 600))

    def claim(self) -> dict[str, Any] | None:
        response = self.client.rpc(
            "claim_processing_task",
            {"p_lease_seconds": self.lease_seconds},
        ).execute()
        return response.data[0] if response.data else None

    def claim_for_job(self, job_id: str, task_type: str | None = None) -> dict[str, Any] | None:
        response = self.client.rpc(
            "claim_processing_task_for_job",
            {
                "p_job_id": job_id,
                "p_lease_seconds": self.lease_seconds,
                "p_task_type": task_type,
            },
        ).execute()
        return response.data[0] if response.data else None

    def heartbeat(self, task: dict[str, Any]) -> bool:
        return self._owned_call(
            "heartbeat_processing_task",
            {
                "p_task_id": task["id"],
                "p_lease_seconds": self.lease_seconds,
            },
            task,
        )

    def complete(self, task: dict[str, Any]) -> bool:
        return self._owned_call("complete_processing_task", {"p_task_id": task["id"]}, task)

    def fail(self, task: dict[str, Any], message: str) -> bool:
        return self._owned_call(
            "fail_processing_task",
            {"p_task_id": task["id"], "p_error_message": message[:1000]},
            task,
        )

    def _owned_call(self, rpc_name: str, args: dict[str, Any], task: dict[str, Any]) -> bool:
        token = task.get("lease_token")
        if not token:
            raise RuntimeError(f"claimed task missing lease_token id={task.get('id')}")
        response = self.client.rpc(
            rpc_name,
            {**args, "p_lease_token": token},
        ).execute()
        return response.data is True
