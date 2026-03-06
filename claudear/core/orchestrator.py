"""Unified TaskOrchestrator for multi-provider task automation."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, TYPE_CHECKING

from claudear.core.types import (
    TaskId,
    TaskStatus,
    ProviderType,
    ProviderInstance,
    UnifiedTask,
)
from claudear.core.state import TaskContext, TaskState, TaskStateMachine
from claudear.core.store import TaskStore, TaskRecord
from claudear.events.types import (
    Event,
    EventType,
    TaskStatusChangedEvent,
    TaskCommentAddedEvent,
)
from claudear.providers.base import PMProvider, EventSource
from claudear.git.worktree import WorktreeManager
from claudear.git.github import GitHubClient

if TYPE_CHECKING:
    from claudear.core.config import MultiProviderSettings

if TYPE_CHECKING:
    from claudear.claude.runner import ClaudeRunner, ClaudeRunnerPool

logger = logging.getLogger(__name__)


@dataclass
class ActiveTask:
    """Represents an active task being worked on."""

    context: TaskContext
    task_id: TaskId
    runner: Optional["ClaudeRunner"] = None


@dataclass
class InstanceResources:
    """Resources specific to a provider instance (team/database)."""

    instance: ProviderInstance
    worktree_manager: WorktreeManager
    github_client: GitHubClient


class TaskOrchestrator:
    """Orchestrates tasks across multiple providers and instances.

    This is the unified task manager that replaces provider-specific
    TaskManagers. It handles:
    - Multiple providers (Linear, Notion)
    - Multiple instances per provider (teams, databases)
    - Per-instance git worktrees and GitHub integration
    - Shared Claude runner pool
    - Event routing and task lifecycle
    """

    def __init__(
        self,
        task_store: TaskStore,
        github_token: Optional[str] = None,
        max_concurrent_tasks: int = 3,
        comment_poll_interval: int = 30,
        blocked_timeout: int = 86400,  # 24 hours
        repo_map: Optional[dict[str, Path]] = None,
        phase_config: Optional[dict[str, dict[str, str]]] = None,
    ):
        """Initialize the orchestrator.

        Args:
            task_store: Shared task persistence store
            github_token: GitHub token for PR operations
            max_concurrent_tasks: Maximum concurrent tasks across all instances
            comment_poll_interval: Seconds between comment polls for blocked tasks
            blocked_timeout: Seconds before a blocked task times out
            repo_map: Mapping of repo keys to local paths
            phase_config: Phase trigger config (state_name -> {phase, command, active_state, complete_state})
        """
        self.store = task_store
        self._github_token = github_token
        self._max_concurrent_tasks = max_concurrent_tasks
        self._comment_poll_interval = comment_poll_interval
        self._blocked_timeout = blocked_timeout
        self._repo_map: dict[str, Path] = repo_map or {}
        self._phase_config: dict[str, dict[str, str]] = phase_config or {}

        # Providers: provider_type -> PMProvider
        self._providers: dict[ProviderType, PMProvider] = {}

        # Instance resources: (provider_type, instance_id) -> InstanceResources
        self._instance_resources: dict[tuple[ProviderType, str], InstanceResources] = {}

        # Active tasks: task_key -> ActiveTask
        self._active_tasks: dict[str, ActiveTask] = {}

        # Per-issue locks for fan-in coordination
        self._issue_locks: dict[str, asyncio.Lock] = {}

        # Claude runner pool (shared across all instances)
        self._runner_pool: Optional["ClaudeRunnerPool"] = None

        # Locks and background tasks (created lazily)
        self._lock: Optional[asyncio.Lock] = None
        self._comment_poll_task: Optional[asyncio.Task] = None

    @staticmethod
    def _log_task_exception(task: asyncio.Task) -> None:
        """Log unhandled exceptions from fire-and-forget asyncio tasks."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error(f"Unhandled exception in background task: {exc}", exc_info=exc)

    def _create_tracked_task(self, coro) -> asyncio.Task:
        """Create an asyncio task with exception logging."""
        task = asyncio.create_task(coro)
        task.add_done_callback(self._log_task_exception)
        return task

    def _get_lock(self) -> asyncio.Lock:
        """Get or create the async lock."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def register_provider(self, provider: PMProvider) -> None:
        """Register a provider with the orchestrator.

        Args:
            provider: Provider to register
        """
        self._providers[provider.provider_type] = provider
        logger.info(f"Registered provider: {provider.display_name}")

    async def register_instance(self, instance: ProviderInstance) -> None:
        """Register and initialize a provider instance.

        Sets up per-instance resources (WorktreeManager, GitHubClient).

        Args:
            instance: Instance to register
        """
        provider = self._providers.get(instance.provider)
        if not provider:
            raise ValueError(f"Provider {instance.provider} not registered")

        # Initialize provider-side instance
        await provider.initialize_instance(instance)

        # Set up per-instance resources
        worktree_manager = WorktreeManager(str(instance.repo_path))
        github_client = GitHubClient(self._github_token)

        resources = InstanceResources(
            instance=instance,
            worktree_manager=worktree_manager,
            github_client=github_client,
        )

        key = (instance.provider, instance.instance_id)
        self._instance_resources[key] = resources

        # Set up event source with our handler
        event_source = provider.get_event_source(instance)
        event_source.set_event_handler(self._handle_event)

        logger.info(
            f"Registered instance: {instance.display_name} "
            f"(repo: {instance.repo_path})"
        )

    async def start(self) -> None:
        """Start the orchestrator and all event sources."""
        # Initialize store
        await self.store.init()

        # Initialize Claude runner pool
        from claudear.claude.runner import ClaudeRunnerPool

        self._runner_pool = ClaudeRunnerPool(self._max_concurrent_tasks)

        # Start event sources for all instances
        for key, resources in self._instance_resources.items():
            provider_type, instance_id = key
            provider = self._providers[provider_type]
            event_source = provider.get_event_source(resources.instance)
            await event_source.start()

        # Recover any in-progress tasks
        await self._recover_tasks()

        # Start comment polling for blocked tasks
        self._comment_poll_task = self._create_tracked_task(self._poll_comments())

        logger.info("Task orchestrator started")

    async def stop(self) -> None:
        """Stop the orchestrator and all event sources."""
        # Stop comment polling
        if self._comment_poll_task:
            self._comment_poll_task.cancel()
            try:
                await self._comment_poll_task
            except asyncio.CancelledError:
                pass

        # Cancel any running tasks
        for composite_key in list(self._active_tasks.keys()):
            if self._runner_pool:
                await self._runner_pool.cancel_runner(composite_key)

        # Stop event sources
        for key, resources in self._instance_resources.items():
            provider_type, instance_id = key
            provider = self._providers[provider_type]
            event_source = provider.get_event_source(resources.instance)
            await event_source.stop()

        # Shutdown providers
        for provider in self._providers.values():
            await provider.shutdown()

        logger.info("Task orchestrator stopped")

    def _get_resources(self, task_id: TaskId) -> Optional[InstanceResources]:
        """Get resources for a task's instance.

        Args:
            task_id: Task identifier

        Returns:
            Instance resources or None if not found
        """
        key = (task_id.provider, task_id.instance_id)
        return self._instance_resources.get(key)

    def _get_provider(self, task_id: TaskId) -> Optional[PMProvider]:
        """Get the provider for a task.

        Args:
            task_id: Task identifier

        Returns:
            Provider or None if not found
        """
        return self._providers.get(task_id.provider)

    # -------------------------------------------------------------------------
    # Event Handling
    # -------------------------------------------------------------------------

    async def _handle_event(self, event: Event) -> None:
        """Handle an event from any provider.

        Routes events to appropriate handlers based on type.

        Args:
            event: Event to handle
        """
        try:
            if event.type == EventType.TASK_STATUS_CHANGED:
                await self._handle_status_change(event)  # type: ignore
            elif event.type == EventType.TASK_COMMENT_ADDED:
                await self._handle_comment(event)  # type: ignore
            else:
                logger.debug(f"Ignoring event type: {event.type}")
        except Exception as e:
            logger.error(f"Error handling event: {e}")

    async def _handle_status_change(self, event: TaskStatusChangedEvent) -> None:
        """Handle a task status change event."""
        task_id = event.task_id

        logger.info(
            f"Status change: {task_id.identifier} "
            f"{event.old_status.value if event.old_status else 'unknown'} -> "
            f"{event.new_status.value}"
            f"{f' (phase: {event.phase})' if event.phase else ''}"
        )

        if event.is_phase_trigger():
            await self._start_phase(
                task_id=task_id,
                phase=event.phase,
                phase_command=event.phase_command,
                repo_keys=event.repo_keys,
                title=event.task_title,
                description=event.task_description,
            )
        elif event.new_status == TaskStatus.DONE:
            await self._handle_done(task_id)
        else:
            logger.debug(
                f"Ignoring non-phase status change for {task_id.identifier}: "
                f"{event.new_status.value}"
            )

    async def _handle_comment(self, event: TaskCommentAddedEvent) -> None:
        """Handle a new comment event."""
        if event.is_bot_comment:
            return

        task_id = event.task_id

        # Find any blocked tasks for this issue
        async with self._get_lock():
            blocked_keys = [
                key for key, at in self._active_tasks.items()
                if at.task_id == task_id and at.context.state == TaskState.BLOCKED
            ]

        if not blocked_keys:
            return

        logger.info(
            f"Human comment on blocked task {task_id.identifier}, "
            f"unblocking {len(blocked_keys)} task(s)"
        )
        for task_key in blocked_keys:
            await self._handle_unblock(task_id, event.comment_body, task_key=task_key)

    def _get_issue_lock(self, issue_id: str) -> asyncio.Lock:
        """Get or create a per-issue lock for fan-in coordination."""
        if issue_id not in self._issue_locks:
            self._issue_locks[issue_id] = asyncio.Lock()
        return self._issue_locks[issue_id]

    # -------------------------------------------------------------------------
    # Phase-Based Task Lifecycle
    # -------------------------------------------------------------------------

    async def _start_phase(
        self,
        task_id: TaskId,
        phase: str,
        phase_command: str,
        repo_keys: list[str],
        title: str,
        description: Optional[str],
    ) -> None:
        """Start a pipeline phase, fanning out to multiple repos.

        Creates one task per repo_key, each with its own worktree and
        Claude session.
        """
        if not repo_keys:
            logger.warning(f"No repo keys for {task_id.identifier}, skipping phase {phase}")
            provider = self._get_provider(task_id)
            if provider:
                await provider.post_comment(
                    task_id,
                    f"**Claudear**: No `repo:X` labels found on this issue. "
                    f"Add repo labels and move back to trigger state to retry.",
                )
            return

        # Determine the active state for this phase
        active_state = None
        for state_name, config in self._phase_config.items():
            if config.get("phase") == phase:
                active_state = config.get("active_state")
                break

        # Move issue to active state
        provider = self._get_provider(task_id)
        if provider and active_state:
            from claudear.providers.linear.provider import LinearProvider
            if isinstance(provider, LinearProvider):
                await provider.update_task_status_by_name(task_id, active_state)

        logger.info(
            f"Starting phase '{phase}' for {task_id.identifier} "
            f"across repos: {repo_keys}"
        )

        # Fan out: create a task for each repo
        for repo_key in repo_keys:
            repo_path = self._repo_map.get(repo_key)
            if not repo_path:
                logger.error(f"Repo key '{repo_key}' not found in REPO_MAP")
                continue

            await self._start_repo_task(
                task_id=task_id,
                phase=phase,
                phase_command=phase_command,
                repo_key=repo_key,
                repo_path=repo_path,
                title=title,
                description=description,
            )

    async def _start_repo_task(
        self,
        task_id: TaskId,
        phase: str,
        phase_command: str,
        repo_key: str,
        repo_path: Path,
        title: str,
        description: Optional[str],
    ) -> None:
        """Start a single repo task within a phase."""
        branch_name = f"{task_id.identifier.lower()}/{repo_key}/{phase}"

        # Create worktree manager for this repo
        worktree_mgr = WorktreeManager(str(repo_path))
        github_client = GitHubClient(self._github_token)

        try:
            worktree_id = f"{task_id.identifier}-{repo_key}-{phase}"
            worktree_path = await worktree_mgr.create(
                worktree_id, branch_name=branch_name
            )

            # Create task context
            context = TaskContext(
                task_id=task_id,
                title=title,
                description=description,
                branch_name=branch_name,
                worktree_path=str(worktree_path),
            )
            context.state_machine.start()

            # Save task record
            record = TaskRecord(
                provider=task_id.provider,
                instance_id=task_id.instance_id,
                external_id=task_id.external_id,
                task_identifier=task_id.identifier,
                repo_key=repo_key,
                phase=phase,
                title=title,
                description=description,
                branch_name=branch_name,
                worktree_path=str(worktree_path),
                state=TaskState.IN_PROGRESS,
                blocked_reason=None,
                blocked_at=None,
                pr_number=None,
                pr_url=None,
                session_id=None,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            await self.store.save(record)

            # Create runner with custom command
            from claudear.claude.runner import ClaudeRunner

            runner = ClaudeRunner(
                working_dir=worktree_path,
                issue_identifier=task_id.identifier,
                title=title,
                description=description,
                command=phase_command,
                on_blocked=lambda reason: self._create_tracked_task(
                    self._handle_blocked(task_id, reason, repo_key=repo_key, phase=phase)
                ),
                on_complete=lambda: self._create_tracked_task(
                    self._handle_phase_task_complete(task_id, repo_key, phase)
                ),
            )

            task_key = record.task_key
            active_task = ActiveTask(
                context=context,
                task_id=task_id,
                runner=runner,
            )

            async with self._get_lock():
                self._active_tasks[task_key] = active_task

            # Run Claude session
            self._create_tracked_task(
                self._run_phase_session(task_id, repo_key, phase, runner)
            )

            logger.info(
                f"Started {phase} task for {task_id.identifier}/{repo_key} "
                f"on branch {branch_name}"
            )

        except Exception as e:
            logger.error(
                f"Failed to start {phase} task for "
                f"{task_id.identifier}/{repo_key}: {e}"
            )

    async def _run_phase_session(
        self,
        task_id: TaskId,
        repo_key: str,
        phase: str,
        runner: "ClaudeRunner",
    ) -> None:
        """Run a Claude session for a phase task."""
        task_key = f"{task_id.composite_key}:{repo_key}:{phase}"
        try:
            result = await runner.run()

            if result.session_id:
                await self.store.update_session_id(task_key, result.session_id)

            if result.is_blocked:
                await self._handle_blocked(
                    task_id, result.blocked_reason,
                    repo_key=repo_key, phase=phase,
                )
            elif result.is_complete:
                await self._handle_phase_task_complete(task_id, repo_key, phase)
            elif result.error:
                await self._handle_error(task_id, result.error, repo_key=repo_key, phase=phase)
            else:
                logger.warning(
                    f"Session for {task_id.identifier}/{repo_key}/{phase} "
                    f"ended without clear state"
                )

        except Exception as e:
            logger.error(f"Phase session failed: {e}")
            await self._handle_error(task_id, str(e), repo_key=repo_key, phase=phase)

    async def _handle_phase_task_complete(
        self,
        task_id: TaskId,
        repo_key: str,
        phase: str,
    ) -> None:
        """Handle completion of a single repo task within a phase.

        Updates the task state, then checks fan-in to see if all repo
        tasks for this phase are done.
        """
        task_key = f"{task_id.composite_key}:{repo_key}:{phase}"
        logger.info(f"Phase task completed: {task_id.identifier}/{repo_key}/{phase}")

        # Update this task's state
        await self.store.update_state(task_key, TaskState.COMPLETED)

        async with self._get_lock():
            if task_key in self._active_tasks:
                del self._active_tasks[task_key]

        # Fan-in check
        await self._check_phase_complete(task_id, phase)

    async def _check_phase_complete(
        self,
        task_id: TaskId,
        phase: str,
    ) -> None:
        """Fan-in: check if all repo tasks for a phase are done.

        If all complete, transition the Linear issue to the next state.
        Uses a per-issue lock to prevent duplicate transitions.
        """
        async with self._get_issue_lock(task_id.external_id):
            all_done, blocked_reason = await self.store.check_phase_complete(
                task_id.external_id, phase
            )

            if blocked_reason:
                logger.info(
                    f"Phase {phase} for {task_id.identifier} has blocked tasks: "
                    f"{blocked_reason}"
                )
                return

            if not all_done:
                logger.debug(
                    f"Phase {phase} for {task_id.identifier} not yet complete"
                )
                return

            # All tasks done - transition the issue
            complete_state = None
            for state_name, config in self._phase_config.items():
                if config.get("phase") == phase:
                    complete_state = config.get("complete_state")
                    break

            if not complete_state:
                logger.warning(f"No complete_state configured for phase {phase}")
                return

            provider = self._get_provider(task_id)
            if provider:
                from claudear.providers.linear.provider import LinearProvider
                if isinstance(provider, LinearProvider):
                    success = await provider.update_task_status_by_name(
                        task_id, complete_state
                    )
                    if success:
                        logger.info(
                            f"Phase {phase} complete for {task_id.identifier}, "
                            f"moved to '{complete_state}'"
                        )
                        await provider.post_comment(
                            task_id,
                            f"**Claudear**: Phase '{phase}' complete for all repos. "
                            f"Issue moved to '{complete_state}'.",
                        )

    async def _handle_blocked(
        self,
        task_id: TaskId,
        reason: Optional[str],
        repo_key: str = "",
        phase: str = "",
    ) -> None:
        """Handle a blocked task."""
        task_key = f"{task_id.composite_key}:{repo_key}:{phase}"
        logger.info(f"Task {task_id.identifier}/{repo_key}/{phase} blocked: {reason}")

        async with self._get_lock():
            active_task = self._active_tasks.get(task_key)
            if not active_task:
                return
            active_task.context.state_machine.block(reason or "Unknown reason")

        await self.store.update_state(task_key, TaskState.BLOCKED, reason)

        provider = self._get_provider(task_id)
        if provider:
            await provider.set_blocked_indicator(task_id, reason)
            await provider.post_comment(
                task_id,
                f"**Claudear is blocked** (repo: {repo_key})\n\n"
                f"**Reason**: {reason or 'Unknown'}\n\n"
                f"Please respond with guidance to continue.",
            )

            # Move issue to Blocked state on the board
            blocked_state = None
            for _, config in self._phase_config.items():
                blocked_state = config.get("blocked_state")
                if blocked_state:
                    break
            if blocked_state:
                from claudear.providers.linear.provider import LinearProvider
                if isinstance(provider, LinearProvider):
                    await provider.update_task_status_by_name(task_id, blocked_state)

    async def _handle_unblock(
        self,
        task_id: TaskId,
        comment_body: str,
        task_key: str = "",
    ) -> None:
        """Handle unblocking a task via human comment."""
        async with self._get_lock():
            active_task = self._active_tasks.get(task_key)
            if not active_task:
                return
            active_task.context.state_machine.unblock()

        await self.store.update_state(task_key, TaskState.IN_PROGRESS)

        provider = self._get_provider(task_id)
        if provider:
            await provider.set_working_indicator(task_id, "Resuming...")

        if active_task.runner:
            result = await active_task.runner.resume(comment_body)

            if result.is_blocked:
                # Extract repo_key and phase from task_key
                parts = task_key.split(":")
                repo_key = parts[-2] if len(parts) >= 5 else ""
                phase = parts[-1] if len(parts) >= 5 else ""
                await self._handle_blocked(
                    task_id, result.blocked_reason,
                    repo_key=repo_key, phase=phase,
                )
            elif result.is_complete:
                parts = task_key.split(":")
                repo_key = parts[-2] if len(parts) >= 5 else ""
                phase = parts[-1] if len(parts) >= 5 else ""
                await self._handle_phase_task_complete(task_id, repo_key, phase)

    async def _handle_done(self, task_id: TaskId) -> None:
        """Handle task marked as done externally - clean up all tasks for this issue."""
        logger.info(f"Issue {task_id.identifier} marked as done")

        # Get all tasks for this issue
        tasks = await self.store.get_by_external_id(task_id.provider, task_id.external_id)
        if not tasks:
            logger.warning(f"No task records found for {task_id.identifier}")
            return

        for task in tasks:
            task_key = task.task_key

            async with self._get_lock():
                if task_key in self._active_tasks:
                    del self._active_tasks[task_key]

            await self.store.update_state(task_key, TaskState.DONE)

        provider = self._get_provider(task_id)
        if provider:
            await provider.clear_indicators(task_id)

    async def _handle_error(
        self,
        task_id: TaskId,
        error: str,
        repo_key: str = "",
        phase: str = "",
    ) -> None:
        """Handle a task error."""
        task_key = f"{task_id.composite_key}:{repo_key}:{phase}"
        logger.error(f"Task {task_id.identifier}/{repo_key}/{phase} failed: {error}")

        async with self._get_lock():
            active_task = self._active_tasks.get(task_key)
            if active_task:
                active_task.context.state_machine.fail(error)
                del self._active_tasks[task_key]

        await self.store.update_state(task_key, TaskState.FAILED, error)

        provider = self._get_provider(task_id)
        if provider:
            await provider.clear_indicators(task_id)
            await provider.post_comment(
                task_id,
                f"**Claudear**: Task failed (repo: {repo_key})\n\n"
                f"**Error**: {error}\n\n"
                f"Please investigate and retry.",
            )

    # -------------------------------------------------------------------------
    # Background Tasks
    # -------------------------------------------------------------------------

    async def _poll_comments(self) -> None:
        """Background task to poll for new comments on blocked tasks."""
        while True:
            try:
                await asyncio.sleep(self._comment_poll_interval)

                blocked_tasks = await self.store.get_blocked_tasks()

                for task in blocked_tasks:
                    if not task.blocked_at:
                        continue

                    blocked_duration = (
                        datetime.now() - task.blocked_at
                    ).total_seconds()
                    if blocked_duration > self._blocked_timeout:
                        task_id = task.task_id
                        await self._handle_error(
                            task_id,
                            f"Blocked for {blocked_duration/3600:.1f} hours without response",
                            repo_key=task.repo_key,
                            phase=task.phase,
                        )
                        continue

                    task_id = task.task_id
                    provider = self._get_provider(task_id)
                    if provider:
                        comments = await provider.get_new_comments(
                            task_id, task.blocked_at
                        )
                        if comments:
                            latest = max(comments, key=lambda c: c["created_at"])
                            await self._handle_unblock(
                                task_id, latest["body"],
                                task_key=task.task_key,
                            )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Comment polling error: {e}")

    async def _recover_tasks(self) -> None:
        """Recover tasks that were in progress when we stopped."""
        active_tasks = await self.store.get_active_tasks()

        for task in active_tasks:
            task_id = task.task_id
            provider = self._get_provider(task_id)

            if task.state == TaskState.IN_PROGRESS:
                logger.warning(
                    f"Task {task.task_identifier}/{task.repo_key}/{task.phase} "
                    f"was in progress, marking as failed"
                )
                await self.store.update_state(
                    task.task_key,
                    TaskState.FAILED,
                    "System restart - please retry",
                )
                if provider:
                    await provider.post_comment(
                        task_id,
                        f"**Claudear**: System restarted while task was in progress "
                        f"(repo: {task.repo_key}). "
                        "Please move back to trigger state to retry.",
                    )

            elif task.state == TaskState.BLOCKED:
                logger.info(
                    f"Task {task.task_identifier}/{task.repo_key} "
                    f"still blocked, will poll for response"
                )

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def get_active_tasks(self) -> list[ActiveTask]:
        """Get all active tasks."""
        return list(self._active_tasks.values())

    def get_instance_info(self) -> list[dict[str, Any]]:
        """Get information about registered instances."""
        return [
            {
                "provider": key[0].value,
                "instance_id": key[1],
                "display_name": resources.instance.display_name,
                "repo_path": str(resources.instance.repo_path) if resources.instance.repo_path else None,
            }
            for key, resources in self._instance_resources.items()
        ]
