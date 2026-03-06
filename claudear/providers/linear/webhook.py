"""Linear webhook event source for multi-team support."""

from __future__ import annotations

import logging
from typing import Callable, Any, Optional, TYPE_CHECKING

from claudear.providers.base import EventSource, EventSourceMode
from claudear.core.types import TaskId, ProviderType, ProviderInstance
from claudear.events.types import (
    Event,
    TaskStatusChangedEvent,
    TaskCommentAddedEvent,
    TaskUpdatedEvent,
)
from claudear.linear.models import WebhookPayload

if TYPE_CHECKING:
    from claudear.providers.linear.provider import LinearProvider

logger = logging.getLogger(__name__)


class LinearWebhookEventSource(EventSource):
    """Webhook event source for a Linear team.

    Converts Linear webhook payloads to unified Event types.
    One instance per team for proper event routing.
    """

    def __init__(
        self,
        provider: "LinearProvider",
        instance: ProviderInstance,
        allowed_assignees: Optional[list[str]] = None,
        valid_repo_keys: Optional[list[str]] = None,
        phase_config: Optional[dict[str, dict[str, str]]] = None,
    ):
        """Initialize webhook event source.

        Args:
            provider: Linear provider instance
            instance: Team/instance configuration
            allowed_assignees: User IDs allowed to trigger automation
            valid_repo_keys: Valid repo keys from REPO_MAP
            phase_config: Phase trigger configuration, e.g.:
                {
                    "Ready for Spec": {"phase": "spec", "command": "generate-spec"},
                    "Ready for Dev": {"phase": "implement", "command": "implement-spec"},
                }
        """
        self._provider = provider
        self._instance = instance
        self._handler: Optional[Callable[[Event], Any]] = None
        self._bot_user_id: Optional[str] = None
        self._allowed_assignees: list[str] = allowed_assignees or []
        self._valid_repo_keys: list[str] = valid_repo_keys or []
        self._phase_config: dict[str, dict[str, str]] = phase_config or {}

    @property
    def mode(self) -> EventSourceMode:
        return EventSourceMode.WEBHOOK

    @property
    def team_id(self) -> str:
        return self._instance.instance_id

    async def start(self) -> None:
        """Start receiving events."""
        try:
            self._bot_user_id = await self._provider.client.get_bot_user_id()
            logger.info(
                f"Linear webhook event source started for team {self.team_id}"
            )
        except Exception as e:
            logger.error(f"Failed to get bot user ID: {e}")

    async def stop(self) -> None:
        logger.info(f"Linear webhook event source stopped for team {self.team_id}")

    def set_event_handler(self, handler: Callable[[Event], Any]) -> None:
        self._handler = handler

    async def handle_webhook(self, payload: WebhookPayload) -> None:
        """Process a Linear webhook payload."""
        if not self._handler:
            logger.warning("No event handler registered, dropping webhook")
            return

        if payload.type == "Issue":
            await self._handle_issue_webhook(payload)
        elif payload.type == "Comment":
            await self._handle_comment_webhook(payload)
        else:
            logger.debug(f"Ignoring webhook type: {payload.type}")

    async def _handle_issue_webhook(self, payload: WebhookPayload) -> None:
        """Handle an issue webhook with intake filtering."""
        issue = payload.get_issue()
        if not issue:
            logger.warning("Could not extract issue from webhook")
            return

        # Verify this issue belongs to our team
        if issue.team_id and issue.team_id != self._instance.instance_id:
            try:
                team_uuid = await self._provider.client.get_team_uuid(
                    self._instance.instance_id
                )
                if issue.team_id != team_uuid:
                    logger.debug(
                        f"Issue {issue.identifier} belongs to team {issue.team_id}, "
                        f"not {self._instance.instance_id}"
                    )
                    return
            except Exception:
                pass

        # --- Intake filter (state-change events only) ---
        is_state_change = payload.action == "update" and payload.is_state_change()
        if is_state_change:
            if not await self._passes_intake_filter(issue.id, issue.identifier):
                return

        # Create task ID
        task_id = TaskId(
            provider=ProviderType.LINEAR,
            instance_id=self._instance.instance_id,
            external_id=issue.id,
            identifier=issue.identifier,
        )

        if is_state_change:
            await self._handle_state_change(payload, task_id, issue)
        elif payload.action in ("create", "update"):
            await self._handle_issue_update(payload, task_id, issue)

    async def _passes_intake_filter(
        self, issue_id: str, identifier: str
    ) -> bool:
        """Check if an issue passes the intake filter.

        Requires:
        1. Issue assignee is in ALLOWED_ASSIGNEES
        2. Issue has the 'claude:auto' label

        Fetches the full issue from the API to check labels and assignee.
        """
        try:
            full_issue = await self._provider.client.get_issue(issue_id)
        except Exception as e:
            logger.warning(f"Failed to fetch issue {identifier} for intake filter: {e}")
            return False

        if not full_issue:
            logger.debug(f"Intake filter: issue {identifier} not found via API")
            return False

        # Check assignee
        if not full_issue.assignee:
            logger.debug(f"Intake filter: {identifier} has no assignee, dropping")
            return False

        if self._allowed_assignees and full_issue.assignee.id not in self._allowed_assignees:
            logger.debug(
                f"Intake filter: {identifier} assignee {full_issue.assignee.id} "
                f"not in allowed list, dropping"
            )
            return False

        # Check for claude:auto label
        try:
            label_names = await self._get_issue_label_names(issue_id)
        except Exception as e:
            logger.warning(f"Failed to fetch labels for {identifier}: {e}")
            return False

        if "claude:auto" not in label_names:
            logger.debug(
                f"Intake filter: {identifier} missing claude:auto label, dropping"
            )
            return False

        return True

    async def _get_issue_label_names(self, issue_id: str) -> list[str]:
        """Fetch label names for an issue from the API."""
        query = """
        query IssueLabels($id: String!) {
            issue(id: $id) {
                labels {
                    nodes {
                        id
                        name
                    }
                }
            }
        }
        """
        result = await self._provider.client._query(query, {"id": issue_id})
        nodes = result.get("issue", {}).get("labels", {}).get("nodes", [])
        return [n["name"] for n in nodes]

    async def resolve_repo_labels(self, issue_id: str, identifier: str) -> list[str]:
        """Resolve repo:X labels on an issue to validated repo keys.

        Fetches labels from the API, extracts those with the "repo:" prefix,
        validates against the configured REPO_MAP keys, and returns the
        resolved keys.

        Args:
            issue_id: Linear issue UUID
            identifier: Issue identifier for logging (e.g. "ENG-123")

        Returns:
            List of valid repo keys found on the issue.
        """
        try:
            label_names = await self._get_issue_label_names(issue_id)
        except Exception as e:
            logger.warning(f"Failed to fetch labels for {identifier}: {e}")
            return []

        repo_keys = []
        for name in label_names:
            if not name.startswith("repo:"):
                continue
            key = name[len("repo:"):]
            if not key:
                continue
            if self._valid_repo_keys and key not in self._valid_repo_keys:
                logger.warning(
                    f"Issue {identifier} has unknown repo label 'repo:{key}', skipping"
                )
                continue
            repo_keys.append(key)

        if not repo_keys:
            logger.warning(f"Issue {identifier} has no repo:X labels")

        return repo_keys

    async def _handle_state_change(
        self,
        payload: WebhookPayload,
        task_id: TaskId,
        issue: Any,
    ) -> None:
        """Handle an issue state change, detecting phase triggers."""
        new_state_id = payload.get_new_state_id()
        old_state_id = payload.get_previous_state_id()

        if not new_state_id:
            logger.warning("Could not determine new state")
            return

        new_status = await self._provider.detect_status(task_id, new_state_id)
        old_status = None
        if old_state_id:
            old_status = await self._provider.detect_status(task_id, old_state_id)

        # Detect phase trigger by matching state name
        phase = None
        phase_command = None
        new_state_name = None
        repo_keys: list[str] = []

        state_info = await self._provider._get_state_info(new_state_id)
        if state_info:
            new_state_name = state_info[0]
            trigger = self._phase_config.get(new_state_name)
            if trigger:
                phase = trigger["phase"]
                phase_command = trigger["command"]
                # Resolve repo labels for phase triggers
                repo_keys = await self.resolve_repo_labels(
                    issue.id, issue.identifier
                )
                logger.info(
                    f"Phase trigger: {issue.identifier} -> {phase} "
                    f"(command: {phase_command}, repos: {repo_keys})"
                )

        event = TaskStatusChangedEvent(
            task_id=task_id,
            timestamp=payload.created_at,
            old_status=old_status,
            new_status=new_status,
            task_title=issue.title,
            task_description=issue.description,
            phase=phase,
            phase_command=phase_command,
            repo_keys=repo_keys,
            new_state_name=new_state_name,
            raw_data={
                "action": payload.action,
                "new_state_id": new_state_id,
                "old_state_id": old_state_id,
            },
        )

        logger.info(
            f"Issue {issue.identifier} state changed: "
            f"{old_status.value if old_status else 'unknown'} -> {new_status.value}"
            f"{f' (phase: {phase})' if phase else ''}"
        )

        await self._dispatch_event(event)

    async def _handle_issue_update(
        self,
        payload: WebhookPayload,
        task_id: TaskId,
        issue: Any,
    ) -> None:
        """Handle a general issue update."""
        updated_fields = []
        if payload.updated_from:
            updated_fields = list(payload.updated_from.keys())

        if "stateId" in updated_fields:
            updated_fields.remove("stateId")

        if not updated_fields:
            return

        event = TaskUpdatedEvent(
            task_id=task_id,
            timestamp=payload.created_at,
            updated_fields=updated_fields,
            new_title=issue.title if "title" in updated_fields else None,
            new_description=issue.description if "description" in updated_fields else None,
            raw_data={"action": payload.action, "updated_from": payload.updated_from},
        )

        logger.debug(f"Issue {issue.identifier} updated: {updated_fields}")
        await self._dispatch_event(event)

    async def _handle_comment_webhook(self, payload: WebhookPayload) -> None:
        """Handle a comment webhook. No intake filter applied."""
        if payload.action != "create":
            return

        comment_data = payload.data
        issue_id = comment_data.get("issueId")
        body = comment_data.get("body", "")
        user_data = comment_data.get("user", {})
        user_id = user_data.get("id", "")
        user_name = user_data.get("name")

        if not issue_id:
            logger.warning("Comment webhook missing issueId")
            return

        is_bot = user_id == self._bot_user_id if self._bot_user_id else False

        task_id = TaskId(
            provider=ProviderType.LINEAR,
            instance_id=self._instance.instance_id,
            external_id=issue_id,
            identifier="",
        )

        event = TaskCommentAddedEvent(
            task_id=task_id,
            timestamp=payload.created_at,
            comment_body=body,
            comment_author_id=user_id,
            comment_author_name=user_name,
            is_bot_comment=is_bot,
            raw_data={"action": payload.action, "comment": comment_data},
        )

        logger.info(
            f"New comment on issue {issue_id} from "
            f"{'bot' if is_bot else user_name or user_id}"
        )

        await self._dispatch_event(event)

    async def _dispatch_event(self, event: Event) -> None:
        """Dispatch an event to the registered handler."""
        if self._handler:
            try:
                result = self._handler(event)
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                logger.error(f"Event handler error: {e}")
