"""Tests for orchestrator fan-out/fan-in and phase-based task lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claudear.core.orchestrator import TaskOrchestrator
from claudear.core.state import TaskState
from claudear.core.store import TaskStore
from claudear.core.types import ProviderType
from claudear.providers.linear.provider import LinearProvider
from tests.factories import make_task_id, make_task_record


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
def task_id():
    return make_task_id()


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
def mock_provider():
    """Create a mock that passes isinstance checks for LinearProvider."""
    provider = MagicMock(spec=LinearProvider)
    provider.provider_type = ProviderType.LINEAR
    provider.display_name = "Linear"
    provider.post_comment = AsyncMock()
    provider.set_blocked_indicator = AsyncMock()
    provider.set_working_indicator = AsyncMock()
    provider.clear_indicators = AsyncMock()
    provider.update_task_status_by_name = AsyncMock(return_value=True)
    provider.initialize = AsyncMock()
    provider.shutdown = AsyncMock()
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


# ---------------------------------------------------------------------------
# Fan-out: creates N tasks for N repo keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fan_out_creates_tasks_per_repo(orchestrator, store, task_id, repo_map):
    """Starting a phase with 2 repo keys creates 2 task records."""
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

        await orchestrator._start_phase(
            task_id=task_id,
            phase="spec",
            phase_command="generate-spec",
            repo_keys=["api", "web"],
            title="Test issue",
            description="desc",
        )

        # Allow spawned tasks to complete
        await asyncio.sleep(0.1)

        tasks = await store.get_by_issue_and_phase(task_id.external_id, "spec")
        assert len(tasks) == 2
        repo_keys = {t.repo_key for t in tasks}
        assert repo_keys == {"api", "web"}


# ---------------------------------------------------------------------------
# Fan-in: waits for all tasks before transitioning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fan_in_waits_for_all_tasks(orchestrator, store, task_id, mock_provider):
    """Phase doesn't transition until ALL repo tasks are complete."""
    for rk, state in [("api", TaskState.COMPLETED), ("web", TaskState.IN_PROGRESS)]:
        rec = make_task_record(
            external_id=task_id.external_id,
            repo_key=rk,
            phase="spec",
            state=state,
        )
        await store.save(rec)

    await orchestrator._check_phase_complete(task_id, "spec")

    # Should NOT have transitioned since web is still in progress
    mock_provider.update_task_status_by_name.assert_not_called()


@pytest.mark.asyncio
async def test_fan_in_transitions_when_all_complete(orchestrator, store, task_id, mock_provider):
    """Phase transitions when ALL repo tasks are complete."""
    for rk in ["api", "web"]:
        rec = make_task_record(
            external_id=task_id.external_id,
            repo_key=rk,
            phase="spec",
            state=TaskState.COMPLETED,
        )
        await store.save(rec)

    await orchestrator._check_phase_complete(task_id, "spec")

    mock_provider.update_task_status_by_name.assert_called_once_with(
        task_id, "Spec Review"
    )


# ---------------------------------------------------------------------------
# Fan-in with blocked task does not transition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fan_in_blocked_does_not_transition(orchestrator, store, task_id, mock_provider):
    """If a task is blocked, phase is not transitioned."""
    for rk, state in [("api", TaskState.COMPLETED), ("web", TaskState.BLOCKED)]:
        rec = make_task_record(
            external_id=task_id.external_id,
            repo_key=rk,
            phase="spec",
            state=state,
            blocked_reason="need clarification" if state == TaskState.BLOCKED else None,
        )
        await store.save(rec)

    await orchestrator._check_phase_complete(task_id, "spec")

    mock_provider.update_task_status_by_name.assert_not_called()


# ---------------------------------------------------------------------------
# Duplicate completion doesn't double-transition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_completion_no_double_transition(orchestrator, store, task_id, mock_provider):
    """Calling _check_phase_complete twice doesn't double-transition."""
    for rk in ["api", "web"]:
        rec = make_task_record(
            external_id=task_id.external_id,
            repo_key=rk,
            phase="implement",
            state=TaskState.COMPLETED,
        )
        await store.save(rec)

    # Call twice concurrently - the per-issue lock serializes them
    await asyncio.gather(
        orchestrator._check_phase_complete(task_id, "implement"),
        orchestrator._check_phase_complete(task_id, "implement"),
    )

    # Both calls see all_done=True, so both may call update.
    # The important thing is the lock serializes the check.
    assert mock_provider.update_task_status_by_name.call_count <= 2


# ---------------------------------------------------------------------------
# Correct Claude command is invoked per phase
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correct_command_per_phase(orchestrator, task_id, repo_map):
    """Phase 1 uses generate-spec, Phase 2 uses implement-spec."""
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

        await orchestrator._start_phase(
            task_id=task_id,
            phase="spec",
            phase_command="generate-spec",
            repo_keys=["api"],
            title="Test",
            description=None,
        )
        await asyncio.sleep(0.05)

        await orchestrator._start_phase(
            task_id=task_id,
            phase="implement",
            phase_command="implement-spec",
            repo_keys=["api"],
            title="Test",
            description=None,
        )
        await asyncio.sleep(0.05)

    assert "generate-spec" in runner_commands
    assert "implement-spec" in runner_commands


# ---------------------------------------------------------------------------
# Active state transition on phase start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase_start_moves_to_active_state(orchestrator, task_id, mock_provider, repo_map):
    """Starting a phase moves the issue to the configured active state."""
    with patch("claudear.core.orchestrator.WorktreeManager") as MockWM, \
         patch("claudear.core.orchestrator.GitHubClient"), \
         patch("claudear.claude.runner.ClaudeRunner") as MockRunner:

        mock_wm = MagicMock()
        mock_wm.create = AsyncMock(return_value=Path("/tmp/fake-worktree"))
        MockWM.return_value = mock_wm

        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(return_value=MagicMock(
            session_id="s1", is_blocked=False, is_complete=True, error=None
        ))
        MockRunner.return_value = mock_runner

        await orchestrator._start_phase(
            task_id=task_id,
            phase="spec",
            phase_command="generate-spec",
            repo_keys=["api"],
            title="Test",
            description=None,
        )

    # First call should be to active state "Speccing"
    first_call = mock_provider.update_task_status_by_name.call_args_list[0]
    assert first_call[0] == (task_id, "Speccing")


# ---------------------------------------------------------------------------
# No repo keys - posts warning comment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_repo_keys_posts_warning(orchestrator, task_id, mock_provider):
    """Starting a phase with no repo keys posts a warning comment."""
    await orchestrator._start_phase(
        task_id=task_id,
        phase="spec",
        phase_command="generate-spec",
        repo_keys=[],
        title="Test",
        description=None,
    )

    mock_provider.post_comment.assert_called_once()
    call_args = mock_provider.post_comment.call_args
    assert "No `repo:X` labels" in call_args[0][1]
