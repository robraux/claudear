"""Test data factories for Claudear tests."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from claudear.core.state import TaskState
from claudear.core.types import ProviderType, TaskId
from claudear.core.store import TaskRecord


def make_task_id(
    *,
    provider: ProviderType = ProviderType.LINEAR,
    instance_id: str = "ENG",
    external_id: str = "issue-uuid-001",
    identifier: str = "ENG-123",
) -> TaskId:
    return TaskId(
        provider=provider,
        instance_id=instance_id,
        external_id=external_id,
        identifier=identifier,
    )


def make_task_record(
    *,
    provider: ProviderType = ProviderType.LINEAR,
    instance_id: str = "ENG",
    external_id: str = "issue-uuid-001",
    task_identifier: str = "ENG-123",
    repo_key: str = "api",
    phase: str = "spec",
    title: str = "Test task",
    description: Optional[str] = "A test task description",
    branch_name: str = "eng-123/api/spec",
    worktree_path: str = "/tmp/worktrees/eng-123",
    state: TaskState = TaskState.PENDING,
    blocked_reason: Optional[str] = None,
    blocked_at: Optional[datetime] = None,
    pr_number: Optional[int] = None,
    pr_url: Optional[str] = None,
    session_id: Optional[str] = None,
    created_at: Optional[datetime] = None,
    updated_at: Optional[datetime] = None,
) -> TaskRecord:
    now = datetime.now()
    return TaskRecord(
        provider=provider,
        instance_id=instance_id,
        external_id=external_id,
        task_identifier=task_identifier,
        repo_key=repo_key,
        phase=phase,
        title=title,
        description=description,
        branch_name=branch_name,
        worktree_path=worktree_path,
        state=state,
        blocked_reason=blocked_reason,
        blocked_at=blocked_at,
        pr_number=pr_number,
        pr_url=pr_url,
        session_id=session_id,
        created_at=created_at or now,
        updated_at=updated_at or now,
    )


def make_webhook_payload(
    *,
    action: str = "update",
    type: str = "Issue",
    data: Optional[dict[str, Any]] = None,
    updated_from: Optional[dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> dict[str, Any]:
    """Build a raw webhook payload dict (pre-parsing)."""
    return {
        "action": action,
        "type": type,
        "data": data or make_issue_data(),
        "updatedFrom": updated_from,
        "createdAt": created_at or datetime.now().isoformat(),
    }


def make_issue_data(
    *,
    id: str = "issue-uuid-001",
    identifier: str = "ENG-123",
    title: str = "Test issue",
    description: Optional[str] = "Test description",
    team_id: str = "team-uuid-123",
    state_id: str = "state-ready-spec",
    assignee_id: Optional[str] = "user-alice",
    label_ids: Optional[list[str]] = None,
    labels: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    """Build issue data as it appears in webhook payloads."""
    data: dict[str, Any] = {
        "id": id,
        "identifier": identifier,
        "title": title,
        "description": description,
        "teamId": team_id,
        "stateId": state_id,
    }
    if assignee_id:
        data["assignee"] = {"id": assignee_id, "name": "Alice"}
    if label_ids:
        data["labelIds"] = label_ids
    if labels:
        data["labels"] = labels
    return data


def make_issue_api_response(
    *,
    id: str = "issue-uuid-001",
    identifier: str = "ENG-123",
    title: str = "Test issue",
    description: Optional[str] = "Test description",
    state_name: str = "Ready for Spec",
    state_type: str = "unstarted",
    state_id: str = "state-ready-spec",
    team_id: str = "team-uuid-123",
    team_key: str = "ENG",
    assignee_id: Optional[str] = "user-alice",
    assignee_name: Optional[str] = "Alice",
    labels: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    """Build an issue response as returned by the Linear API."""
    result: dict[str, Any] = {
        "id": id,
        "identifier": identifier,
        "title": title,
        "description": description,
        "priority": 0,
        "state": {"id": state_id, "name": state_name, "type": state_type},
        "team": {"id": team_id, "name": "Engineering", "key": team_key},
        "createdAt": datetime.now().isoformat(),
        "updatedAt": datetime.now().isoformat(),
    }
    if assignee_id:
        result["assignee"] = {"id": assignee_id, "name": assignee_name}
    if labels:
        result["labels"] = {"nodes": labels}
    return result
