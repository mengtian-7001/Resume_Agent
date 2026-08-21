#!/usr/bin/env python3
"""Local smoke test: Supabase auth/upload + optional Worker one-click parse."""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx

CONFIG_PATH = ROOT / "supabase-config.js"
SAMPLE_JD = ROOT / "samples/jd-ai-agent-llm.docx"
SAMPLE_RESUME = ROOT / "samples/resume-cv_001.docx"
WORKER_URL = os.environ.get("WORKER_URL", "http://127.0.0.1:8000")


def read_config() -> dict[str, str]:
    text = CONFIG_PATH.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for key in ("url", "anonKey", "workspaceId"):
        match = re.search(rf'{key}\s*:\s*"([^"]+)"', text)
        if match:
            out[key] = match.group(1)
    return out


def ok(label: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"✓ {label}{suffix}")


def fail(label: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"✗ {label}{suffix}")


def main() -> int:
    print("=== 简历中台本地冒烟测试 ===\n")
    errors = 0

    if not CONFIG_PATH.exists():
        fail("supabase-config.js", "文件不存在")
        return 1
    cfg = read_config()
    for key in ("url", "anonKey", "workspaceId"):
        if not cfg.get(key):
            fail(f"配置缺失 {key}")
            errors += 1
    if errors:
        return 1
    ok("读取 supabase-config.js")

    for sample in (SAMPLE_JD, SAMPLE_RESUME):
        if not sample.exists():
            fail("样例文件", str(sample.name))
            errors += 1
    if errors:
        return 1
    ok("样例 JD/简历文件存在")

    worker_ok = False
    try:
        health = httpx.get(f"{WORKER_URL}/health", timeout=3.0)
        if health.status_code == 200:
            ok("Worker /health", health.json().get("agent_mode", "ok"))
            worker_ok = True
        else:
            fail("Worker /health", f"HTTP {health.status_code}")
    except Exception as exc:
        fail("Worker /health", f"未启动 ({exc})")

    upload_errors = 0
    base = cfg["url"].rstrip("/")
    anon = cfg["anonKey"]
    workspace_id = cfg["workspaceId"]

    with httpx.Client(base_url=base, timeout=30.0) as client:
        auth = client.post(
            "/auth/v1/signup",
            headers={"apikey": anon, "Content-Type": "application/json"},
            json={},
        )
        if auth.status_code not in (200, 201):
            fail("匿名登录", f"HTTP {auth.status_code} {auth.text[:200]}")
            return 1
        session = auth.json()
        access_token = session["access_token"]
        user_id = session["user"]["id"]
        headers = {"apikey": anon, "Authorization": f"Bearer {access_token}"}
        ok("匿名登录", user_id[:8])

        bootstrap = client.post(
            "/rest/v1/rpc/bootstrap_anonymous_workspace",
            headers={**headers, "Content-Type": "application/json", "Prefer": "return=minimal"},
            json={"target_workspace_id": workspace_id},
        )
        if bootstrap.status_code >= 400:
            bootstrap = client.post(
                "/rest/v1/rpc/bootstrap_anonymous_workspace",
                headers={**headers, "Content-Type": "application/json", "Prefer": "return=minimal"},
                json={},
            )
        if bootstrap.status_code >= 400:
            fail("bootstrap workspace", f"HTTP {bootstrap.status_code} {bootstrap.text[:200]}")
            errors += 1
        else:
            ok("bootstrap workspace")

        job_resp = client.post(
            "/rest/v1/screening_jobs",
            headers={**headers, "Content-Type": "application/json", "Prefer": "return=representation"},
            json={
                "workspace_id": workspace_id,
                "title": "smoke-test",
                "status": "uploading",
                "created_by": user_id,
            },
        )
        if job_resp.status_code >= 400:
            fail("创建 screening_job", f"HTTP {job_resp.status_code} {job_resp.text[:200]}")
            return 1
        job = job_resp.json()[0]
        job_id = job["id"]
        ok("创建 screening_job", job_id[:8])

        def upload_doc(doc_type: str, path: Path) -> None:
            nonlocal upload_errors
            storage_path = f"{workspace_id}/{job_id}/{doc_type}/{uuid.uuid4()}-{path.name}"
            file_bytes = path.read_bytes()
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            up = client.post(
                f"/storage/v1/object/screening-documents/{storage_path}",
                headers={
                    **headers,
                    "Content-Type": mime,
                    "x-upsert": "false",
                },
                content=file_bytes,
            )
            if up.status_code >= 400:
                fail(f"上传 {doc_type}", f"HTTP {up.status_code} {up.text[:200]}")
                upload_errors += 1
                return
            doc = client.post(
                "/rest/v1/documents",
                headers={**headers, "Content-Type": "application/json", "Prefer": "return=minimal"},
                json={
                    "workspace_id": workspace_id,
                    "screening_job_id": job_id,
                    "document_type": doc_type,
                    "original_filename": path.name,
                    "storage_path": storage_path,
                    "mime_type": mime,
                    "size_bytes": len(file_bytes),
                    "status": "validated",
                },
            )
            if doc.status_code >= 400:
                fail(f"写入 documents/{doc_type}", f"HTTP {doc.status_code} {doc.text[:200]}")
                upload_errors += 1
            else:
                ok(f"上传 {doc_type}", path.name)

        upload_doc("jd", SAMPLE_JD)
        upload_doc("resume", SAMPLE_RESUME)
        if upload_errors:
            return 1

        start = client.post(
            "/rest/v1/rpc/start_screening",
            headers={**headers, "Content-Type": "application/json", "Prefer": "return=minimal"},
            json={"target_job_id": job_id},
        )
        if start.status_code >= 400:
            fail("start_screening", f"HTTP {start.status_code} {start.text[:200]}")
            return 1
        ok("start_screening", "任务已入队")

    if not worker_ok:
        print("\n上传链路已通过，但 Worker 未运行或未配置 backend/.env（需要 service_role key）。")
        print("请运行：export SUPABASE_SERVICE_ROLE_KEY='你的key' && ./dev.sh")
        return 1

    try:
        parse = httpx.post(
            f"{WORKER_URL}/dev/jobs/process",
            json={"job_id": job_id},
            timeout=120.0,
        )
        if parse.status_code == 200:
            result = parse.json()
            status = result.get("status")
            if status == "completed":
                ok("一键解析", f"processed_tasks={result.get('processed_tasks')}")
                print(f"\n全部通过。job_id={job_id}")
                return 0
            fail("一键解析", f"status={status} job={json.dumps(result.get('job', {}), ensure_ascii=False)[:200]}")
            return 1
        fail("一键解析", f"HTTP {parse.status_code} {parse.text[:200]}")
        return 1
    except Exception as exc:
        fail("一键解析", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
