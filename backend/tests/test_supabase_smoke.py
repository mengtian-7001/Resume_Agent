"""Opt-in live Supabase smoke checks; never run against production by default."""

from __future__ import annotations

import os
import uuid

import httpx
import pytest


def _skip_unless_live() -> None:
    if os.getenv("RUN_SUPABASE_SMOKE") != "1":
        pytest.skip("set RUN_SUPABASE_SMOKE=1 with dedicated test-project credentials")


def _require_live_keys() -> dict[str, str]:
    _skip_unless_live()
    url = os.getenv("SUPABASE_URL") or ""
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""
    user_id = os.getenv("SUPABASE_SMOKE_USER_ID") or ""
    anon_key = os.getenv("SUPABASE_ANON_KEY") or ""
    user_jwt = os.getenv("SUPABASE_SMOKE_USER_JWT") or ""
    if not url or not service_key or not user_id:
        pytest.skip("need SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and SUPABASE_SMOKE_USER_ID")
    return {
        "url": url,
        "service_key": service_key,
        "user_id": user_id,
        "anon_key": anon_key,
        "user_jwt": user_jwt,
    }


def _require_user_jwt(keys: dict[str, str]) -> None:
    if not keys["anon_key"] or not keys["user_jwt"]:
        pytest.skip("need SUPABASE_ANON_KEY and SUPABASE_SMOKE_USER_JWT for RLS tests")


def _user_client(url: str, anon_key: str, user_jwt: str):
    from supabase import create_client

    client = create_client(url, anon_key)
    client.postgrest.auth(user_jwt)
    return client


def _raise_if_error(response: object) -> object:
    error = getattr(response, "error", None)
    if error:
        raise RuntimeError(error)
    return response


@pytest.mark.integration
def test_authenticated_supabase_rest_smoke() -> None:
    """Verify an explicitly supplied user JWT can reach RLS-protected REST."""
    _skip_unless_live()

    url = os.environ["SUPABASE_URL"].rstrip("/")
    anon_key = os.environ["SUPABASE_ANON_KEY"]
    user_jwt = os.environ["SUPABASE_SMOKE_USER_JWT"]
    response = httpx.get(
        f"{url}/rest/v1/screening_jobs?select=id&limit=1",
        headers={"apikey": anon_key, "Authorization": f"Bearer {user_jwt}"},
        timeout=15,
    )
    assert response.status_code == 200, response.text[:500]


@pytest.mark.integration
def test_supabase_match_checker_and_feedback_pipeline() -> None:
    """Worker-like persist uses service_role; recruiter_feedback insert uses the user JWT."""
    keys = _require_live_keys()
    _require_user_jwt(keys)

    from supabase import create_client

    from app.agents import MockCheckerAgent, MockConstructionAgent
    from app.checker_harness import run_checker_harness
    from app.persist import persist_candidate_core
    from app.worker import ScreeningWorker

    service = create_client(keys["url"], keys["service_key"])
    user = _user_client(keys["url"], keys["anon_key"], keys["user_jwt"])
    suffix = uuid.uuid4().hex[:8]
    workspace_id = None
    job_id = None
    try:
        workspace = service.table("workspaces").insert({"name": f"e2e-{suffix}"}).execute().data[0]
        workspace_id = workspace["id"]
        service.table("workspace_members").insert(
            {"workspace_id": workspace_id, "user_id": keys["user_id"], "role": "recruiter"}
        ).execute()
        job = service.table("screening_jobs").insert(
            {
                "workspace_id": workspace_id,
                "title": "AI Agent 工程师",
                "created_by": keys["user_id"],
                "status": "processing",
            }
        ).execute().data[0]
        job_id = job["id"]
        jd_doc = service.table("documents").insert(
            {
                "workspace_id": workspace_id,
                "screening_job_id": job_id,
                "document_type": "jd",
                "original_filename": f"jd-{suffix}.docx",
                "storage_path": f"{workspace_id}/{job_id}/jd-{suffix}.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "size_bytes": 1024,
                "status": "parsed",
                "extracted_text": "岗位名称：AI Agent 工程师。本科，3年。要求 Python、FastAPI。",
            }
        ).execute().data[0]
        resume_doc = service.table("documents").insert(
            {
                "workspace_id": workspace_id,
                "screening_job_id": job_id,
                "document_type": "resume",
                "original_filename": f"resume-{suffix}.docx",
                "storage_path": f"{workspace_id}/{job_id}/resume-{suffix}.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "size_bytes": 1024,
                "status": "parsed",
                "extracted_text": "负责生产环境 FastAPI 与 Python 服务上线。",
            }
        ).execute().data[0]
        profile_row = service.table("candidate_profiles").insert(
            {
                "screening_job_id": job_id,
                "source_document_id": resume_doc["id"],
                "display_name": "E2E 候选人",
                "profile": {
                    "name": "E2E 候选人",
                    "years_experience": 4,
                    "education": "本科",
                    "skills": ["Python", "FastAPI"],
                },
            }
        ).execute().data[0]

        requirements = ScreeningWorker._extract_requirements(jd_doc["extracted_text"])
        profile = {
            **profile_row["profile"],
            "raw_text": resume_doc["extracted_text"],
            "id": profile_row["id"],
        }
        construction = MockConstructionAgent()
        output = construction.analyze(requirements, profile)
        harness = run_checker_harness(
            initial_output=output,
            requirements=requirements,
            raw_candidate_profile=profile,
            review=MockCheckerAgent().review,
            revise=lambda issues: construction.analyze(requirements, profile, revision_feedback=issues),
        )
        persist_candidate_core(
            service,
            workspace_id=workspace_id,
            screening_job_id=job_id,
            candidate_profile_id=profile_row["id"],
            match_payload=harness.output.match_result,
            questions=harness.output.questions,
            followups=harness.output.followups,
            review=harness.review,
        )
        feedback = _raise_if_error(
            user.table("recruiter_feedback").insert(
                {
                    "workspace_id": workspace_id,
                    "screening_job_id": job_id,
                    "candidate_profile_id": profile_row["id"],
                    "feedback_type": "evidence",
                    "value": "confirmed",
                    "job_title": "AI Agent 工程师",
                    "skills": ["Python", "FastAPI"],
                    "polarity": "positive",
                    "created_by": keys["user_id"],
                }
            ).execute()
        )
        assert getattr(feedback, "data", None)

        match = service.table("match_results").select("id,decision,score").eq(
            "candidate_profile_id", profile_row["id"]
        ).execute().data
        questions = service.table("question_packs").select("id").eq(
            "candidate_profile_id", profile_row["id"]
        ).execute().data
        reviews = service.table("checker_reviews").select("id,status").eq(
            "candidate_profile_id", profile_row["id"]
        ).execute().data
        stored_feedback = user.table("recruiter_feedback").select("id,polarity").eq(
            "candidate_profile_id", profile_row["id"]
        ).execute().data
        assert match and questions and reviews and stored_feedback
    finally:
        if job_id:
            service.table("screening_jobs").delete().eq("id", job_id).execute()
        if workspace_id:
            service.table("workspaces").delete().eq("id", workspace_id).execute()


@pytest.mark.integration
def test_recruiter_can_start_screening_and_viewer_cannot_write_feedback() -> None:
    """User JWT must pass recruiter RLS; service_role is only for fixture setup, claim, and cleanup."""
    keys = _require_live_keys()
    _require_user_jwt(keys)

    from supabase import create_client

    from app.task_lease import TaskLeaseClient

    service = create_client(keys["url"], keys["service_key"])
    user = _user_client(keys["url"], keys["anon_key"], keys["user_jwt"])
    suffix = uuid.uuid4().hex[:8]
    workspace_id = None
    job_id = None
    try:
        workspace = service.table("workspaces").insert({"name": f"rls-{suffix}"}).execute().data[0]
        workspace_id = workspace["id"]
        service.table("workspace_members").insert(
            {"workspace_id": workspace_id, "user_id": keys["user_id"], "role": "recruiter"}
        ).execute()
        job = service.table("screening_jobs").insert(
            {
                "workspace_id": workspace_id,
                "title": "RLS 工程师",
                "created_by": keys["user_id"],
                "status": "uploading",
            }
        ).execute().data[0]
        job_id = job["id"]
        service.table("documents").insert(
            {
                "workspace_id": workspace_id,
                "screening_job_id": job_id,
                "document_type": "jd",
                "original_filename": f"jd-{suffix}.docx",
                "storage_path": f"{workspace_id}/{job_id}/jd-{suffix}.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "size_bytes": 1024,
                "status": "pending",
            }
        ).execute()
        resume_doc = service.table("documents").insert(
            {
                "workspace_id": workspace_id,
                "screening_job_id": job_id,
                "document_type": "resume",
                "original_filename": f"resume-{suffix}.docx",
                "storage_path": f"{workspace_id}/{job_id}/resume-{suffix}.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "size_bytes": 1024,
                "status": "pending",
            }
        ).execute().data[0]
        profile_row = service.table("candidate_profiles").insert(
            {
                "screening_job_id": job_id,
                "source_document_id": resume_doc["id"],
                "display_name": "RLS 候选人",
                "profile": {"name": "RLS 候选人", "skills": ["Python"]},
            }
        ).execute().data[0]

        _raise_if_error(user.rpc("start_screening", {"target_job_id": job_id}).execute())
        queued = (
            service.table("processing_tasks")
            .select("id,task_type,status")
            .eq("screening_job_id", job_id)
            .eq("task_type", "parse_jd")
            .execute()
            .data
        )
        assert queued, "recruiter start_screening should enqueue parse_jd"
        claimed = TaskLeaseClient(service, 60).claim_for_job(job_id, task_type="parse_jd")
        assert claimed and claimed.get("task_type") == "parse_jd"

        payload = {
            "workspace_id": workspace_id,
            "screening_job_id": job_id,
            "candidate_profile_id": profile_row["id"],
            "feedback_type": "decision",
            "value": "accurate",
            "job_title": "RLS 工程师",
            "skills": ["Python"],
            "created_by": keys["user_id"],
        }
        inserted = _raise_if_error(user.table("recruiter_feedback").insert(payload).execute())
        assert getattr(inserted, "data", None)

        service.table("workspace_members").update({"role": "viewer"}).eq(
            "workspace_id", workspace_id
        ).eq("user_id", keys["user_id"]).execute()

        denied = False
        try:
            response = user.table("recruiter_feedback").insert(
                {**payload, "value": "too_high", "polarity": "negative_calibration"}
            ).execute()
            if getattr(response, "error", None) or not getattr(response, "data", None):
                denied = True
        except Exception:
            denied = True
        assert denied, "viewer must not insert recruiter_feedback"

        readable = (
            user.table("recruiter_feedback")
            .select("id")
            .eq("candidate_profile_id", profile_row["id"])
            .execute()
            .data
        )
        assert readable, "viewer should still read recruiter_feedback"
    finally:
        if job_id:
            service.table("screening_jobs").delete().eq("id", job_id).execute()
        if workspace_id:
            service.table("workspaces").delete().eq("id", workspace_id).execute()
