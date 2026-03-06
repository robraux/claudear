"""Shared fixtures for Claudear tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from claudear.core.store import TaskStore


@pytest.fixture
def tmp_db(tmp_path):
    """Provide a temporary SQLite database path."""
    return str(tmp_path / "test.db")


@pytest.fixture
async def task_store(tmp_db):
    """Provide an initialized TaskStore with a temp database."""
    store = TaskStore(db_path=tmp_db)
    await store.init()
    return store


@pytest.fixture
def mock_linear_client():
    """Provide a mock LinearClient with common methods stubbed."""
    client = AsyncMock()
    client.get_bot_user_id = AsyncMock(return_value="bot-user-id")
    client.get_team_uuid = AsyncMock(return_value="team-uuid-123")
    client.get_workflow_states = AsyncMock(return_value={
        "Backlog": "state-backlog",
        "Todo": "state-todo",
        "Ready for Spec": "state-ready-spec",
        "Spec in Progress": "state-spec-progress",
        "Spec Review": "state-spec-review",
        "Ready for Dev": "state-ready-dev",
        "Dev in Progress": "state-dev-progress",
        "In Review": "state-in-review",
        "Blocked": "state-blocked",
        "Done": "state-done",
    })
    client.get_issue = AsyncMock(return_value=None)
    client.post_comment = AsyncMock(return_value=None)
    client.update_issue_state = AsyncMock(return_value=True)
    client.get_issue_labels = AsyncMock(return_value=[])
    client.add_label_to_issue = AsyncMock(return_value=True)
    client.remove_label_from_issue = AsyncMock(return_value=True)
    client._query = AsyncMock(return_value={})
    return client


@pytest.fixture
def sample_repo_map(tmp_path):
    """Provide a sample REPO_MAP with real temp directories as git repos."""
    api_repo = tmp_path / "repos" / "api"
    web_repo = tmp_path / "repos" / "web"
    for repo in [api_repo, web_repo]:
        repo.mkdir(parents=True)
        (repo / ".git").mkdir()
    return {
        "api": str(api_repo),
        "web": str(web_repo),
    }


@pytest.fixture
def sample_allowed_assignees():
    """Provide a sample ALLOWED_ASSIGNEES list."""
    return ["user-alice", "user-bob"]
