import unittest
from unittest.mock import MagicMock

from app.agent_trace import AgentRunTracer


class AgentRunTracerStatusTests(unittest.TestCase):
    def _tracer_with_store(self) -> tuple[AgentRunTracer, dict]:
        store: dict = {}
        client = MagicMock()

        def upsert(payload, on_conflict=None):
            store["last"] = dict(payload)
            store.setdefault("history", []).append(dict(payload))
            return MagicMock(execute=MagicMock(return_value=MagicMock(data=[payload])))

        table = MagicMock()
        table.upsert.side_effect = upsert
        def execute_fetch(*_args, **_kwargs):
            payload = store.get("last")
            return MagicMock(data=[dict(payload)] if payload else [])

        table.select.return_value.eq.return_value.limit.return_value.execute.side_effect = execute_fetch
        client.table.return_value = table
        tracer = AgentRunTracer(client, agent_mode="mock")
        return tracer, store

    def test_fail_sets_failed_status_and_stage(self) -> None:
        tracer, store = self._tracer_with_store()
        tracer.fail("ws", "job-1", reason="core persist incomplete", merge_state={"persist_errors": ["x"]})
        payload = store["last"]
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["state"]["stage"], "failed")
        self.assertTrue(payload["state"]["failed"])
        self.assertIn("core persist", payload["state"]["failure_reason"])
        self.assertTrue(payload.get("completed_at"))

    def test_complete_does_not_leave_failed_stage(self) -> None:
        tracer, store = self._tracer_with_store()
        tracer.complete("ws", "job-2", merge_state={"candidate_count": 1})
        payload = store["last"]
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["state"]["stage"], "completed")
        self.assertNotEqual(payload["state"].get("stage"), "failed")

    def test_fail_never_writes_completed_status(self) -> None:
        tracer, store = self._tracer_with_store()
        tracer.fail("ws", "job-3", reason="boom")
        self.assertEqual(store["last"]["status"], "failed")
        self.assertNotEqual(store["last"]["status"], "completed")
        self.assertEqual(store["last"]["state"]["stage"], "failed")

    def test_append_does_not_merge_running_steps_across_candidates(self) -> None:
        tracer, store = self._tracer_with_store()
        tracer.append_step(
            "ws",
            "job-4",
            {
                "id": "construction.analyze",
                "label": "Construction 匹配与出题 · 韩沐辰",
                "status": "running",
                "candidate_id": "c1",
                "candidate_name": "韩沐辰",
            },
        )
        tracer.append_step(
            "ws",
            "job-4",
            {
                "id": "construction.analyze",
                "label": "Construction 匹配与出题 · 孙博文",
                "status": "running",
                "candidate_id": "c2",
                "candidate_name": "孙博文",
            },
        )
        steps = store["last"]["state"]["steps"]
        self.assertEqual(len(steps), 2)
        self.assertEqual([step["candidate_name"] for step in steps], ["韩沐辰", "孙博文"])
        self.assertTrue(all(step["status"] == "running" for step in steps))

        tracer.append_step(
            "ws",
            "job-4",
            {
                "id": "construction.analyze",
                "label": "Construction 匹配与出题 · 韩沐辰",
                "status": "completed",
                "candidate_id": "c1",
                "candidate_name": "韩沐辰",
                "duration_ms": 12,
            },
        )
        steps = store["last"]["state"]["steps"]
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["status"], "completed")
        self.assertEqual(steps[0]["duration_ms"], 12)
        self.assertEqual(steps[1]["status"], "running")
        self.assertEqual(steps[1]["candidate_name"], "孙博文")


if __name__ == "__main__":
    unittest.main()
