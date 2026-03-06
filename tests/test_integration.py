"""Integration tests for the full webhook -> orchestrator -> store pipeline."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claudear.core.orchestrator import TaskOrchestrator
from claudear.core.state import TaskState
from claudear.core.store import TaskStore
from claudear.core.types import ProviderType, ProviderInstance
from claudear.events.types import TaskStatus
from claudear.linear.models import Issue, WebhookPayload, WorkflowState, User
from claudear.providers.linear.provider import LinearProvider
from claudear.providers.linear.webhook import LinearWebhookEventSource


PHASE_CONFIG = {
    "Ready for Spec": {
        "phase": "spec",
        "command": "generate-spec",
        "active_state": "Speccing",
        "complete_state": "Spec Review",
        "blocked_state": "Blocked",
    },
    "Ready for Dev": {
        "phase": "implement",
        "command": "implement-spec",
        "active_state": "In Progress",
        "complete_state": "In Review",
        "blocked_state": "Blocked",
    },
}


@pytest.fixture
def repo_map(tmp_path):
    api = tmp_path / "api"
    api.mkdir()
    (api / ".git").mkdir()
    web = tmp_path / "web"
    web.mkdir()
    (web / ".git").mkdir()
    return {"api": api, "web": web}


@pytest.fixture
def instance(tmp_path):
    return ProviderInstance(
        provider=ProviderType.LINEAR,
        instance_id="ENG",
        display_name="Engineering",
        repo_path=tmp_path / "default-repo",
    )


@pytest.fixture
def mock_provider():
    provider = MagicMock(spec=LinearProvider)
    provider.provider_type = ProviderType.LINEAR
    provider.display_name = "Linear"
    provider.post_comment = AsyncMock()
    provider.set_blocked_indicator = AsyncMock()
    provider.set_working_indicator = AsyncMock()
    provider.clear_indicators = AsyncMock()
    provider.update_task_status_by_name = AsyncMock(return_value=True)
    provider.detect_status = AsyncMock(return_value=TaskStatus.TODO)
    provider._get_state_info = AsyncMock(return_value=None)
    provider.client = MagicMock()
    provider.client.get_bot_user_id = AsyncMock(return_value="bot-user-1")
    provider.client.get_issue = AsyncMock(return_value=None)
    provider.client._query = AsyncMock(return_value={})
    provider.client.get_team_uuid = AsyncMock(return_value="team-uuid-123")
    return provider


@pytest.fixture
async def store(tmp_path):
    s = TaskStore(str(tmp_path / "test.db"))
    await s.init()
    return s


@pytest.fixture
def orchestrator(store, repo_map, mock_provider):
    orch = TaskOrchestrator(
        task_store=store,
        github_token="fake-token",
        max_concurrent_tasks=5,
        repo_map=repo_map,
        phase_config=PHASE_CONFIG,
    )
    orch._providers[ProviderType.LINEAR] = mock_provider
    return orch


@pytest.fixture
def event_source(mock_provider, instance):
    source = LinearWebhookEventSource(
        provider=mock_provider,
        instance=instance,
        allowed_assignees=["user-alice"],
        valid_repo_keys=["api", "web"],
        phase_config=PHASE_CONFIG,
    )
    return source


def _make_payload(
    issue_id: str = "issue-uuid-001",
    identifier: str = "ENG-123",
    title: str = "Add user auth",
    state_id: str = "state-ready-spec",
    old_state_id: str = "state-backlog",
) -> WebhookPayload:
    return WebhookPayload(
        action="update",
        type="Issue",
        data={
            "id": issue_id,
            "identifier": identifier,
            "title": title,
            "description": "Test description",
            "teamId": "team-uuid-123",
            "stateId": state_id,
        },
        updatedFrom={"stateId": old_state_id},
        createdAt=datetime.now(),
    )


def _setup_mock_issue(mock_provider, issue_id, identifier, title, state_name, labels):
    """Configure mock_provider to return a specific issue and labels."""
    mock_issue = Issue(
        id=issue_id,
        identifier=identifier,
        title=title,
        description="Test description",
        state=WorkflowState(id="state-1", name=state_name, type="unstarted"),
        assignee=User(id="user-alice", name="Alice"),
    )
    mock_provider.client.get_issue = AsyncMock(return_value=mock_issue)

    label_nodes = [{"id": f"l{i}", "name": name} for i, name in enumerate(labels)]
    mock_provider.client._query = AsyncMock(return_value={
        "issue": {"labels": {"nodes": label_nodes}}
    })

    mock_provider._get_state_info = AsyncMock(
        return_value=(state_name, "unstarted")
    )
    mock_provider.detect_status = AsyncMock(return_value=TaskStatus.TODO)


# ---------------------------------------------------------------------------
# 10.4: Webhook for "Ready for Spec" with two repo labels
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spec_phase_with_two_repos(
    event_source, orchestrator, store, mock_provider
):
    """Mock a webhook for 'Ready for Spec' with two repo labels.
    Verify two tasks created and issue moved to 'Spec Review' on completion.
    """
    event_source.set_event_handler(orchestrator._handle_event)

    _setup_mock_issue(
        mock_provider,
        issue_id="issue-uuid-001",
        identifier="ENG-123",
        title="Add user auth",
        state_name="Ready for Spec",
        labels=["claude:auto", "repo:api", "repo:web"],
    )

    with patch("claudear.core.orchestrator.WorktreeManager") as MockWM, \
         patch("claudear.core.orchestrator.GitHubClient"), \
         patch("claudear.claude.runner.ClaudeRunner") as MockRunner:

        mock_wm = MagicMock()
        mock_wm.create = AsyncMock(return_value=Path("/tmp/fake-worktree"))
        MockWM.return_value = mock_wm

        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(return_value=MagicMock(
            session_id="sess-1", is_blocked=False, is_complete=True, error=None
        ))
        MockRunner.return_value = mock_runner

        await event_source.handle_webhook(_make_payload())
        await asyncio.sleep(0.2)

    # Two tasks should have been created
    tasks = await store.get_by_issue_and_phase("issue-uuid-001", "spec")
    assert len(tasks) == 2
    assert {t.repo_key for t in tasks} == {"api", "web"}

    # Both tasks completed, so issue should be moved to "Spec Review"
    calls = mock_provider.update_task_status_by_name.call_args_list
    state_names = [c[0][1] for c in calls]
    assert "Speccing" in state_names
    assert "Spec Review" in state_names


# ---------------------------------------------------------------------------
# 10.5: Webhook for "Ready for Dev" (Phase 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_implement_phase_with_correct_command(
    event_source, orchestrator, store, mock_provider
):
    """Mock a webhook for 'Ready for Dev'.
    Verify Phase 2 tasks created with correct command invocation.
    """
    event_source.set_event_handler(orchestrator._handle_event)

    _setup_mock_issue(
        mock_provider,
        issue_id="issue-uuid-002",
        identifier="ENG-456",
        title="Build API",
        state_name="Ready for Dev",
        labels=["claude:auto", "repo:api"],
    )

    runner_commands = []

    with patch("claudear.core.orchestrator.WorktreeManager") as MockWM, \
         patch("claudear.core.orchestrator.GitHubClient"), \
         patch("claudear.claude.runner.ClaudeRunner") as MockRunner:

        mock_wm = MagicMock()
        mock_wm.create = AsyncMock(return_value=Path("/tmp/fake-worktree"))
        MockWM.return_value = mock_wm

        def capture_runner(*args, **kwargs):
            runner_commands.append(kwargs.get("command"))
            mock = MagicMock()
            mock.run = AsyncMock(return_value=MagicMock(
                session_id="s1", is_blocked=False, is_complete=True, error=None
            ))
            return mock

        MockRunner.side_effect = capture_runner

        payload = _make_payload(
            issue_id="issue-uuid-002",
            identifier="ENG-456",
            title="Build API",
            state_id="state-ready-dev",
            old_state_id="state-spec-review",
        )
        await event_source.handle_webhook(payload)
        await asyncio.sleep(0.2)

    assert "implement-spec" in runner_commands

    calls = mock_provider.update_task_status_by_name.call_args_list
    state_names = [c[0][1] for c in calls]
    assert "In Progress" in state_names  # active state
    assert "In Review" in state_names  # complete state


# ---------------------------------------------------------------------------
# 10.6: One task blocked - issue moves to "Blocked"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_task_moves_issue_to_blocked(
    event_source, orchestrator, store, mock_provider
):
    """Mock a webhook for 'Ready for Spec' where one task gets blocked.
    Verify issue moves to 'Blocked' and comment posted.
    """
    event_source.set_event_handler(orchestrator._handle_event)

    _setup_mock_issue(
        mock_provider,
        issue_id="issue-uuid-003",
        identifier="ENG-789",
        title="Fix bug",
        state_name="Ready for Spec",
        labels=["claude:auto", "repo:api", "repo:web"],
    )

    call_count = 0

    with patch("claudear.core.orchestrator.WorktreeManager") as MockWM, \
         patch("claudear.core.orchestrator.GitHubClient"), \
         patch("claudear.claude.runner.ClaudeRunner") as MockRunner:

        mock_wm = MagicMock()
        mock_wm.create = AsyncMock(return_value=Path("/tmp/fake-worktree"))
        MockWM.return_value = mock_wm

        def make_runner(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock = MagicMock()
            if call_count == 1:
                mock.run = AsyncMock(return_value=MagicMock(
                    session_id="s1", is_blocked=False, is_complete=True, error=None
                ))
            else:
                mock.run = AsyncMock(return_value=MagicMock(
                    session_id="s2", is_blocked=True,
                    blocked_reason="Need API docs", is_complete=False, error=None
                ))
            return mock

        MockRunner.side_effect = make_runner

        payload = _make_payload(
            issue_id="issue-uuid-003",
            identifier="ENG-789",
            title="Fix bug",
        )
        await event_source.handle_webhook(payload)
        await asyncio.sleep(0.3)

    # Should have moved to "Blocked" state
    calls = mock_provider.update_task_status_by_name.call_args_list
    state_names = [c[0][1] for c in calls]
    assert "Blocked" in state_names

    # Should have posted a blocked comment
    comment_calls = mock_provider.post_comment.call_args_list
    blocked_comments = [c for c in comment_calls if "blocked" in c[0][1].lower()]
    assert len(blocked_comments) > 0
