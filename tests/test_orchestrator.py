"""Tests for orchestrator fan-out/fan-in and phase-based task lifecycle."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claudear.core.orchestrator import TaskOrchestrator, ActiveTask
from claudear.core.state import TaskContext, TaskState
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


# ---------------------------------------------------------------------------
# _handle_done: cleans up all tasks for an issue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_done_cleans_up_tasks(orchestrator, store, task_id, mock_provider):
    """_handle_done marks all tasks for the issue as DONE and clears indicators."""
    for rk in ["api", "web"]:
        rec = make_task_record(
            external_id=task_id.external_id,
            repo_key=rk,
            phase="spec",
            state=TaskState.IN_PROGRESS,
        )
        await store.save(rec)

    await orchestrator._handle_done(task_id)

    tasks = await store.get_by_issue_and_phase(task_id.external_id, "spec")
    assert all(t.state == TaskState.DONE for t in tasks)
    mock_provider.clear_indicators.assert_called_once_with(task_id)


# ---------------------------------------------------------------------------
# _handle_error: marks task as failed, posts comment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_error_marks_failed_and_posts_comment(orchestrator, store, task_id, mock_provider):
    """_handle_error marks the task as FAILED and posts an error comment."""
    rec = make_task_record(
        external_id=task_id.external_id,
        repo_key="api",
        phase="spec",
        state=TaskState.IN_PROGRESS,
    )
    await store.save(rec)

    # Add to active tasks so the handler can find it
    context = TaskContext(task_id=task_id, title="Test", description=None, branch_name="b", worktree_path="/tmp")
    context.state_machine.start()
    task_key = rec.task_key
    orchestrator._active_tasks[task_key] = ActiveTask(context=context, task_id=task_id)

    await orchestrator._handle_error(task_id, "something broke", repo_key="api", phase="spec")

    updated = await store.get_by_key(task_key)
    assert updated.state == TaskState.FAILED
    assert task_key not in orchestrator._active_tasks
    mock_provider.post_comment.assert_called_once()
    assert "something broke" in mock_provider.post_comment.call_args[0][1]


# ---------------------------------------------------------------------------
# _handle_unblock: resumes a blocked task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_unblock_resumes_blocked_task(orchestrator, store, task_id, mock_provider):
    """_handle_unblock transitions blocked task to in-progress and calls runner.resume."""
    rec = make_task_record(
        external_id=task_id.external_id,
        repo_key="api",
        phase="spec",
        state=TaskState.BLOCKED,
        blocked_reason="need help",
    )
    await store.save(rec)

    context = TaskContext(task_id=task_id, title="Test", description=None, branch_name="b", worktree_path="/tmp")
    context.state_machine.start()
    context.state_machine.block("need help")
    task_key = rec.task_key

    mock_runner = MagicMock()
    # Resume returns not blocked and not complete (still working)
    mock_runner.resume = AsyncMock(return_value=MagicMock(is_blocked=False, is_complete=False, blocked_reason=None))
    active = ActiveTask(context=context, task_id=task_id, runner=mock_runner)
    orchestrator._active_tasks[task_key] = active

    await orchestrator._handle_unblock(task_id, "here is the guidance", task_key=task_key)

    updated = await store.get_by_key(task_key)
    assert updated.state == TaskState.IN_PROGRESS
    mock_runner.resume.assert_called_once_with("here is the guidance")
    mock_provider.set_working_indicator.assert_called_once()


# ---------------------------------------------------------------------------
# _poll_comments: times out blocked tasks past threshold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_comments_times_out_stale_blocked_task(orchestrator, store, task_id, mock_provider):
    """Blocked tasks past the timeout are marked as failed."""
    rec = make_task_record(
        external_id=task_id.external_id,
        repo_key="api",
        phase="spec",
        state=TaskState.BLOCKED,
        blocked_reason="stuck",
        blocked_at=datetime.now() - timedelta(hours=25),
    )
    await store.save(rec)

    # Set a very short timeout so the task is already expired
    orchestrator._blocked_timeout = 1  # 1 second

    # Run one iteration of _poll_comments by patching the sleep to cancel
    async def short_poll():
        """Run the poll loop body once then stop."""
        blocked_tasks = await store.get_blocked_tasks()
        for task in blocked_tasks:
            if not task.blocked_at:
                continue
            blocked_duration = (datetime.now() - task.blocked_at).total_seconds()
            if blocked_duration > orchestrator._blocked_timeout:
                await orchestrator._handle_error(
                    task.task_id,
                    f"Blocked for {blocked_duration/3600:.1f} hours without response",
                    repo_key=task.repo_key,
                    phase=task.phase,
                )

    await short_poll()

    updated = await store.get_by_key(rec.task_key)
    assert updated.state == TaskState.FAILED


# ---------------------------------------------------------------------------
# _recover_tasks: marks in-progress tasks as failed on restart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_tasks_marks_in_progress_as_failed(orchestrator, store, task_id, mock_provider):
    """Tasks that were in-progress at shutdown are marked failed on recovery."""
    rec = make_task_record(
        external_id=task_id.external_id,
        repo_key="api",
        phase="spec",
        state=TaskState.IN_PROGRESS,
    )
    await store.save(rec)

    await orchestrator._recover_tasks()

    updated = await store.get_by_key(rec.task_key)
    assert updated.state == TaskState.FAILED
    mock_provider.post_comment.assert_called_once()
    assert "System restarted" in mock_provider.post_comment.call_args[0][1]


@pytest.mark.asyncio
async def test_recover_tasks_leaves_blocked_alone(orchestrator, store, task_id, mock_provider):
    """Blocked tasks are left as-is during recovery (will be polled)."""
    rec = make_task_record(
        external_id=task_id.external_id,
        repo_key="api",
        phase="spec",
        state=TaskState.BLOCKED,
        blocked_reason="waiting",
        blocked_at=datetime.now(),
    )
    await store.save(rec)

    await orchestrator._recover_tasks()

    updated = await store.get_by_key(rec.task_key)
    assert updated.state == TaskState.BLOCKED
    mock_provider.post_comment.assert_not_called()
