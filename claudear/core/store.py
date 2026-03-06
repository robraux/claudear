"""SQLite persistence for multi-provider task state."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiosqlite

from claudear.core.state import TaskState
from claudear.core.types import ProviderType, TaskId

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2


@dataclass
class TaskRecord:
    """Database record for a task.

    Supports multi-provider, multi-instance, multi-repo storage.
    Composite key: provider:instance:external_id:repo_key
    """

    # Provider identification
    provider: ProviderType
    instance_id: str  # Team ID (e.g. "ENG")
    external_id: str  # Provider's native UUID (issue UUID)

    # Task identification
    task_identifier: str  # Human-readable: ENG-123

    # Multi-repo and phase support
    repo_key: str  # Which repo this task targets (e.g. "api", "web")
    phase: str  # Pipeline phase (e.g. "spec", "implement")

    # Task content
    title: str
    description: Optional[str]

    # Git/PR tracking
    branch_name: str
    worktree_path: str
    pr_number: Optional[int]
    pr_url: Optional[str]

    # State
    state: TaskState
    blocked_reason: Optional[str]
    blocked_at: Optional[datetime]

    # Claude session
    session_id: Optional[str]

    # Timestamps
    created_at: datetime
    updated_at: datetime

    @property
    def task_key(self) -> str:
        """Composite key: 'provider:instance:external:repo_key:phase'."""
        return f"{self.provider.value}:{self.instance_id}:{self.external_id}:{self.repo_key}:{self.phase}"

    @property
    def instance_key(self) -> str:
        """Instance key: 'provider:instance'."""
        return f"{self.provider.value}:{self.instance_id}"

    @property
    def task_id(self) -> TaskId:
        """Get TaskId for this record."""
        return TaskId(
            provider=self.provider,
            instance_id=self.instance_id,
            external_id=self.external_id,
            identifier=self.task_identifier,
        )


class TaskStore:
    """SQLite-based persistence for multi-provider task records.

    Supports:
    - Multiple providers (Linear)
    - Multiple instances per provider (teams)
    - Multiple tasks per issue (one per repo_key)
    - Phase tracking (spec, implement)
    - Composite primary key: provider:instance:external:repo_key
    """

    def __init__(self, db_path: str = "claudear.db"):
        self.db_path = Path(db_path)
        self._initialized = False

    async def init(self) -> None:
        """Initialize database schema with migration support."""
        if self._initialized:
            return

        async with aiosqlite.connect(self.db_path) as db:
            # Check schema version
            await self._migrate_if_needed(db)
            await db.commit()

        self._initialized = True
        logger.info(f"Initialized task store at {self.db_path}")

    async def _migrate_if_needed(self, db: aiosqlite.Connection) -> None:
        """Check schema version and migrate if needed."""
        # Create version table if it doesn't exist
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL
            )
        """)

        cursor = await db.execute("SELECT version FROM schema_version")
        row = await cursor.fetchone()
        current_version = row[0] if row else 0

        if current_version < SCHEMA_VERSION:
            if current_version > 0:
                logger.warning(
                    f"Migrating task store from v{current_version} to v{SCHEMA_VERSION}. "
                    "Existing task data will be lost."
                )
                await db.execute("DROP TABLE IF EXISTS tasks")

            await self._create_schema(db)

            # Update version
            await db.execute("DELETE FROM schema_version")
            await db.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )

    async def _create_schema(self, db: aiosqlite.Connection) -> None:
        """Create the tasks table with multi-repo support."""
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_key TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                instance_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                task_identifier TEXT NOT NULL,
                repo_key TEXT NOT NULL,
                phase TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                branch_name TEXT NOT NULL,
                worktree_path TEXT NOT NULL,
                state TEXT NOT NULL,
                blocked_reason TEXT,
                blocked_at TEXT,
                pr_number INTEGER,
                pr_url TEXT,
                session_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_state
            ON tasks(state)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_provider
            ON tasks(provider)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_instance
            ON tasks(provider, instance_id)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_identifier
            ON tasks(task_identifier)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_external_phase
            ON tasks(external_id, phase)
        """)

    async def save(self, task: TaskRecord) -> None:
        """Save or update a task record."""
        await self.init()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO tasks (
                    task_key, provider, instance_id, external_id, task_identifier,
                    repo_key, phase,
                    title, description, branch_name, worktree_path,
                    state, blocked_reason, blocked_at,
                    pr_number, pr_url, session_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    task.task_key,
                    task.provider.value,
                    task.instance_id,
                    task.external_id,
                    task.task_identifier,
                    task.repo_key,
                    task.phase,
                    task.title,
                    task.description,
                    task.branch_name,
                    task.worktree_path,
                    task.state.value,
                    task.blocked_reason,
                    task.blocked_at.isoformat() if task.blocked_at else None,
                    task.pr_number,
                    task.pr_url,
                    task.session_id,
                    task.created_at.isoformat(),
                    datetime.now().isoformat(),
                ),
            )
            await db.commit()

        logger.debug(f"Saved task {task.task_identifier}:{task.repo_key} in state {task.state.value}")

    async def get(
        self,
        task_id: TaskId,
        repo_key: str,
        phase: str,
    ) -> Optional[TaskRecord]:
        """Get a task by TaskId, repo_key, and phase."""
        task_key = f"{task_id.composite_key}:{repo_key}:{phase}"
        return await self.get_by_key(task_key)

    async def get_by_key(self, task_key: str) -> Optional[TaskRecord]:
        """Get a task by composite key."""
        await self.init()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM tasks WHERE task_key = ?", (task_key,)
            )
            row = await cursor.fetchone()

            if row:
                return self._row_to_record(row)
            return None

    async def get_by_external_id(
        self, provider: ProviderType, external_id: str
    ) -> list[TaskRecord]:
        """Get all tasks for a provider's native ID (e.g. all repo tasks for one issue).

        Returns a list since one issue can have multiple repo tasks.
        """
        await self.init()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM tasks WHERE provider = ? AND external_id = ?",
                (provider.value, external_id),
            )
            rows = await cursor.fetchall()
            return [self._row_to_record(row) for row in rows]

    async def get_by_issue_and_phase(
        self, external_id: str, phase: str
    ) -> list[TaskRecord]:
        """Get all tasks for an issue in a specific phase.

        Used for fan-in: checking if all repo tasks for a given
        issue and phase are complete.
        """
        await self.init()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM tasks WHERE external_id = ? AND phase = ?",
                (external_id, phase),
            )
            rows = await cursor.fetchall()
            return [self._row_to_record(row) for row in rows]

    async def check_phase_complete(
        self, external_id: str, phase: str
    ) -> tuple[bool, Optional[str]]:
        """Check if all tasks for an issue+phase are complete.

        Returns:
            (all_complete, blocked_reason) - True if all tasks completed/done,
            or a blocked reason string if any task is blocked.
        """
        tasks = await self.get_by_issue_and_phase(external_id, phase)
        if not tasks:
            return False, "No tasks found"

        blocked_repos = []
        all_terminal = True

        for task in tasks:
            if task.state == TaskState.BLOCKED:
                blocked_repos.append(f"{task.repo_key}: {task.blocked_reason or 'unknown'}")
            elif task.state not in (TaskState.COMPLETED, TaskState.DONE, TaskState.IN_REVIEW):
                all_terminal = False

        if blocked_repos:
            return False, "Blocked in: " + "; ".join(blocked_repos)

        return all_terminal, None

    async def get_by_identifier(self, identifier: str) -> list[TaskRecord]:
        """Get all tasks by human-readable identifier (e.g. "ENG-123").

        Returns a list since one issue can have tasks for multiple repos.
        """
        await self.init()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM tasks WHERE task_identifier = ?", (identifier,)
            )
            rows = await cursor.fetchall()
            return [self._row_to_record(row) for row in rows]

    async def get_by_state(
        self,
        state: TaskState,
        provider: Optional[ProviderType] = None,
        instance_id: Optional[str] = None,
    ) -> list[TaskRecord]:
        """Get all tasks in a specific state."""
        await self.init()

        query = "SELECT * FROM tasks WHERE state = ?"
        params: list = [state.value]

        if provider:
            query += " AND provider = ?"
            params.append(provider.value)
            if instance_id:
                query += " AND instance_id = ?"
                params.append(instance_id)

        query += " ORDER BY updated_at DESC"

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [self._row_to_record(row) for row in rows]

    async def get_blocked_tasks(
        self,
        provider: Optional[ProviderType] = None,
        instance_id: Optional[str] = None,
    ) -> list[TaskRecord]:
        """Get all blocked tasks."""
        return await self.get_by_state(TaskState.BLOCKED, provider, instance_id)

    async def get_active_tasks(
        self,
        provider: Optional[ProviderType] = None,
        instance_id: Optional[str] = None,
    ) -> list[TaskRecord]:
        """Get all active (non-terminal) tasks."""
        await self.init()

        active_states = [
            TaskState.PENDING.value,
            TaskState.IN_PROGRESS.value,
            TaskState.BLOCKED.value,
            TaskState.COMPLETED.value,
            TaskState.IN_REVIEW.value,
        ]

        query = f"SELECT * FROM tasks WHERE state IN ({','.join(['?'] * len(active_states))})"
        params: list = active_states.copy()

        if provider:
            query += " AND provider = ?"
            params.append(provider.value)
            if instance_id:
                query += " AND instance_id = ?"
                params.append(instance_id)

        query += " ORDER BY updated_at DESC"

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [self._row_to_record(row) for row in rows]

    async def get_by_instance(
        self, provider: ProviderType, instance_id: str, limit: int = 100
    ) -> list[TaskRecord]:
        """Get all tasks for a specific instance."""
        await self.init()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM tasks
                WHERE provider = ? AND instance_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (provider.value, instance_id, limit),
            )
            rows = await cursor.fetchall()
            return [self._row_to_record(row) for row in rows]

    async def get_all(self, limit: int = 100) -> list[TaskRecord]:
        """Get all tasks."""
        await self.init()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,)
            )
            rows = await cursor.fetchall()
            return [self._row_to_record(row) for row in rows]

    async def delete(self, task_id: TaskId, repo_key: str, phase: str) -> bool:
        """Delete a task record."""
        task_key = f"{task_id.composite_key}:{repo_key}:{phase}"
        return await self.delete_by_key(task_key)

    async def delete_by_key(self, task_key: str) -> bool:
        """Delete a task by composite key."""
        await self.init()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM tasks WHERE task_key = ?", (task_key,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def update_state(
        self,
        task_key: str,
        state: TaskState,
        blocked_reason: Optional[str] = None,
    ) -> bool:
        """Update just the state of a task.

        Args:
            task_key: Composite key (provider:instance:external:repo_key)
            state: New state
            blocked_reason: Reason if blocking

        Returns:
            True if updated
        """
        await self.init()

        blocked_at = datetime.now().isoformat() if state == TaskState.BLOCKED else None

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE tasks
                SET state = ?, blocked_reason = ?, blocked_at = ?, updated_at = ?
                WHERE task_key = ?
            """,
                (
                    state.value,
                    blocked_reason,
                    blocked_at,
                    datetime.now().isoformat(),
                    task_key,
                ),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def update_pr_info(
        self, task_key: str, pr_number: int, pr_url: str
    ) -> bool:
        """Update PR information for a task."""
        await self.init()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE tasks
                SET pr_number = ?, pr_url = ?, updated_at = ?
                WHERE task_key = ?
            """,
                (pr_number, pr_url, datetime.now().isoformat(), task_key),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def update_session_id(
        self, task_key: str, session_id: str
    ) -> bool:
        """Update Claude session ID for a task."""
        await self.init()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE tasks
                SET session_id = ?, updated_at = ?
                WHERE task_key = ?
            """,
                (session_id, datetime.now().isoformat(), task_key),
            )
            await db.commit()
            return cursor.rowcount > 0

    def _row_to_record(self, row: aiosqlite.Row) -> TaskRecord:
        """Convert a database row to a TaskRecord."""
        return TaskRecord(
            provider=ProviderType(row["provider"]),
            instance_id=row["instance_id"],
            external_id=row["external_id"],
            task_identifier=row["task_identifier"],
            repo_key=row["repo_key"],
            phase=row["phase"],
            title=row["title"],
            description=row["description"],
            branch_name=row["branch_name"],
            worktree_path=row["worktree_path"],
            state=TaskState(row["state"]),
            blocked_reason=row["blocked_reason"],
            blocked_at=(
                datetime.fromisoformat(row["blocked_at"])
                if row["blocked_at"]
                else None
            ),
            pr_number=row["pr_number"],
            pr_url=row["pr_url"],
            session_id=row["session_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
