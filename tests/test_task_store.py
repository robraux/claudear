"""Tests for the multi-repo TaskStore."""

from __future__ import annotations

import pytest

from claudear.core.state import TaskState
from claudear.core.store import TaskStore, SCHEMA_VERSION

from tests.factories import make_task_record


# -- Basic CRUD --


@pytest.mark.asyncio
async def test_save_and_get_by_key(task_store):
    """Save a task and retrieve it by composite key."""
    task = make_task_record(repo_key="api", phase="spec")
    await task_store.save(task)

    result = await task_store.get_by_key(task.task_key)
    assert result is not None
    assert result.repo_key == "api"
    assert result.phase == "spec"
    assert result.task_identifier == "ENG-123"


@pytest.mark.asyncio
async def test_task_key_includes_repo_key(task_store):
    """Task key format is provider:instance:external:repo_key:phase."""
    task = make_task_record(repo_key="web", phase="spec")
    assert task.task_key == "linear:ENG:issue-uuid-001:web:spec"


# -- Multiple tasks per issue --


@pytest.mark.asyncio
async def test_two_tasks_same_issue_different_repos(task_store):
    """Same issue can have tasks for different repos."""
    task_api = make_task_record(repo_key="api", branch_name="eng-123/api/spec")
    task_web = make_task_record(repo_key="web", branch_name="eng-123/web/spec")

    await task_store.save(task_api)
    await task_store.save(task_web)

    result_api = await task_store.get_by_key(task_api.task_key)
    result_web = await task_store.get_by_key(task_web.task_key)

    assert result_api is not None
    assert result_web is not None
    assert result_api.repo_key == "api"
    assert result_web.repo_key == "web"


# -- Query by issue + phase --


@pytest.mark.asyncio
async def test_get_by_issue_and_phase(task_store):
    """Query all tasks for an issue in a specific phase."""
    task_api = make_task_record(repo_key="api", phase="spec")
    task_web = make_task_record(repo_key="web", phase="spec")
    task_api_impl = make_task_record(repo_key="api", phase="implement")

    await task_store.save(task_api)
    await task_store.save(task_web)
    await task_store.save(task_api_impl)

    spec_tasks = await task_store.get_by_issue_and_phase("issue-uuid-001", "spec")
    assert len(spec_tasks) == 2
    assert {t.repo_key for t in spec_tasks} == {"api", "web"}

    impl_tasks = await task_store.get_by_issue_and_phase("issue-uuid-001", "implement")
    assert len(impl_tasks) == 1
    assert impl_tasks[0].repo_key == "api"


# -- Fan-in: check_phase_complete --


@pytest.mark.asyncio
async def test_fan_in_all_complete(task_store):
    """Fan-in returns True when all tasks in a phase are complete."""
    task_api = make_task_record(repo_key="api", phase="spec", state=TaskState.COMPLETED)
    task_web = make_task_record(repo_key="web", phase="spec", state=TaskState.COMPLETED)

    await task_store.save(task_api)
    await task_store.save(task_web)

    complete, reason = await task_store.check_phase_complete("issue-uuid-001", "spec")
    assert complete is True
    assert reason is None


@pytest.mark.asyncio
async def test_fan_in_one_blocked(task_store):
    """Fan-in returns blocked reason when one task is blocked."""
    task_api = make_task_record(repo_key="api", phase="spec", state=TaskState.COMPLETED)
    task_web = make_task_record(
        repo_key="web", phase="spec",
        state=TaskState.BLOCKED, blocked_reason="needs clarification",
    )

    await task_store.save(task_api)
    await task_store.save(task_web)

    complete, reason = await task_store.check_phase_complete("issue-uuid-001", "spec")
    assert complete is False
    assert "web" in reason
    assert "needs clarification" in reason


@pytest.mark.asyncio
async def test_fan_in_one_in_progress(task_store):
    """Fan-in returns False when one task is still in progress."""
    task_api = make_task_record(repo_key="api", phase="spec", state=TaskState.COMPLETED)
    task_web = make_task_record(repo_key="web", phase="spec", state=TaskState.IN_PROGRESS)

    await task_store.save(task_api)
    await task_store.save(task_web)

    complete, reason = await task_store.check_phase_complete("issue-uuid-001", "spec")
    assert complete is False
    assert reason is None


@pytest.mark.asyncio
async def test_fan_in_no_tasks(task_store):
    """Fan-in with no tasks returns False."""
    complete, reason = await task_store.check_phase_complete("nonexistent", "spec")
    assert complete is False
    assert "No tasks found" in reason


# -- Schema migration --


@pytest.mark.asyncio
async def test_schema_version_set(task_store, tmp_db):
    """Schema version should be set after init."""
    import aiosqlite

    async with aiosqlite.connect(tmp_db) as db:
        cursor = await db.execute("SELECT version FROM schema_version")
        row = await cursor.fetchone()
        assert row[0] == SCHEMA_VERSION


@pytest.mark.asyncio
async def test_schema_migration_from_old(tmp_db):
    """Migrating from old schema drops and recreates the table."""
    import aiosqlite

    # Simulate old schema (v1)
    async with aiosqlite.connect(tmp_db) as db:
        await db.execute("""
            CREATE TABLE schema_version (version INTEGER NOT NULL)
        """)
        await db.execute("INSERT INTO schema_version (version) VALUES (1)")
        await db.execute("""
            CREATE TABLE tasks (
                task_key TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                instance_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                task_identifier TEXT NOT NULL,
                title TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        await db.commit()

    # Init should migrate
    store = TaskStore(db_path=tmp_db)
    await store.init()

    # Should be able to save a new-format task
    task = make_task_record()
    await task_store_save_helper(store, task)

    result = await store.get_by_key(task.task_key)
    assert result is not None
    assert result.repo_key == "api"


async def task_store_save_helper(store, task):
    """Helper to save a task (avoids fixture dependency)."""
    await store.save(task)


# -- get_by_external_id returns list --


@pytest.mark.asyncio
async def test_get_by_external_id_returns_multiple(task_store):
    """get_by_external_id returns all tasks for an issue across repos."""
    from claudear.core.types import ProviderType

    task_api = make_task_record(repo_key="api")
    task_web = make_task_record(repo_key="web")

    await task_store.save(task_api)
    await task_store.save(task_web)

    results = await task_store.get_by_external_id(ProviderType.LINEAR, "issue-uuid-001")
    assert len(results) == 2


# -- update_state --


@pytest.mark.asyncio
async def test_update_state(task_store):
    """update_state changes state by task_key."""
    task = make_task_record(state=TaskState.PENDING)
    await task_store.save(task)

    updated = await task_store.update_state(task.task_key, TaskState.IN_PROGRESS)
    assert updated is True

    result = await task_store.get_by_key(task.task_key)
    assert result.state == TaskState.IN_PROGRESS


# -- get_by_identifier returns list --


@pytest.mark.asyncio
async def test_get_by_identifier_returns_list(task_store):
    """get_by_identifier returns all tasks for that identifier."""
    task_api = make_task_record(repo_key="api")
    task_web = make_task_record(repo_key="web")

    await task_store.save(task_api)
    await task_store.save(task_web)

    results = await task_store.get_by_identifier("ENG-123")
    assert len(results) == 2
