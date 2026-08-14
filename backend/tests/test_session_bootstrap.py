from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role")
os.environ.setdefault("INTERNAL_API_TOKEN", "test-internal-token")

from app import main  # noqa: E402


class _FakeTable:
    def __init__(self, name: str, writes: list[tuple[str, dict[str, str], str | None]]):
        self.name = name
        self.writes = writes

    def upsert(self, payload: dict[str, str], *, on_conflict: str | None = None):
        self.writes.append((self.name, payload, on_conflict))
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class _FakeClient:
    def __init__(self, user: object):
        self.auth = SimpleNamespace(get_user=lambda _token: SimpleNamespace(user=user))
        self.writes: list[tuple[str, dict[str, str], str | None]] = []

    def table(self, name: str):
        return _FakeTable(name, self.writes)


def test_anonymous_workspace_id_is_stable_and_user_scoped():
    first = main._anonymous_workspace_id("user-a")
    assert first == main._anonymous_workspace_id("user-a")
    assert first != main._anonymous_workspace_id("user-b")


def test_bootstrap_anonymous_session_creates_private_workspace(monkeypatch: pytest.MonkeyPatch):
    user = SimpleNamespace(id="89c1f35d-7ac3-46bd-b3c1-f65daf096de8", is_anonymous=True)
    client = _FakeClient(user)
    monkeypatch.setattr(main, "ScreeningWorker", lambda: SimpleNamespace(client=client))

    result = main.bootstrap_anonymous_session("Bearer anonymous-token")

    assert result["mode"] == "anonymous"
    assert result["workspace_id"] == main._anonymous_workspace_id(str(user.id))
    assert client.writes == [
        (
            "workspaces",
            {"id": result["workspace_id"], "name": f"匿名体验 {str(user.id)[:8]}"},
            "id",
        ),
        (
            "workspace_members",
            {"workspace_id": result["workspace_id"], "user_id": str(user.id), "role": "recruiter"},
            "workspace_id,user_id",
        ),
    ]


def test_bootstrap_rejects_non_anonymous_user(monkeypatch: pytest.MonkeyPatch):
    user = SimpleNamespace(id="member-user", is_anonymous=False)
    client = _FakeClient(user)
    monkeypatch.setattr(main, "ScreeningWorker", lambda: SimpleNamespace(client=client))

    with pytest.raises(HTTPException) as exc_info:
        main.bootstrap_anonymous_session("Bearer member-token")

    assert exc_info.value.status_code == 403
    assert client.writes == []
