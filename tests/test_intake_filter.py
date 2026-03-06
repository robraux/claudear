"""Tests for intake filter logic in LinearWebhookEventSource."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from claudear.core.types import ProviderType, ProviderInstance
from claudear.linear.models import WebhookPayload
from claudear.providers.linear.webhook import LinearWebhookEventSource

from tests.factories import make_issue_data, make_issue_api_response


@pytest.fixture
def instance():
    return ProviderInstance(
        provider=ProviderType.LINEAR,
        instance_id="ENG",
        display_name="Linear/ENG",
        repo_path=None,
    )


@pytest.fixture
def mock_provider(mock_linear_client):
    provider = MagicMock()
    provider.client = mock_linear_client
    provider.detect_status = AsyncMock(return_value=MagicMock(value="todo"))
    provider._get_state_info = AsyncMock(return_value=None)
    return provider


@pytest.fixture
def event_source(mock_provider, instance):
    source = LinearWebhookEventSource(
        provider=mock_provider,
        instance=instance,
        allowed_assignees=["user-alice", "user-bob"],
    )
    source._handler = AsyncMock()
    source._bot_user_id = "bot-user-id"
    return source


def _make_state_change_payload(
    issue_data: dict | None = None,
    old_state_id: str = "state-old",
    new_state_id: str = "state-new",
) -> WebhookPayload:
    """Build a webhook payload representing a state change."""
    data = issue_data or make_issue_data()
    data["stateId"] = new_state_id
    return WebhookPayload(
        action="update",
        type="Issue",
        data=data,
        updatedFrom={"stateId": old_state_id},
        createdAt=datetime.now(),
    )


def _make_comment_payload(issue_id: str = "issue-uuid-001") -> WebhookPayload:
    """Build a webhook payload for a new comment."""
    return WebhookPayload(
        action="create",
        type="Comment",
        data={
            "id": "comment-001",
            "body": "Hello from user",
            "issueId": issue_id,
            "user": {"id": "user-alice", "name": "Alice"},
        },
        createdAt=datetime.now(),
    )


# -- Passes filter --


@pytest.mark.asyncio
async def test_allowed_assignee_with_auto_label_passes(event_source, mock_provider):
    """Issue with allowed assignee and claude:auto label should dispatch an event."""
    api_issue = MagicMock()
    api_issue.assignee = MagicMock(id="user-alice")
    mock_provider.client.get_issue = AsyncMock(return_value=api_issue)

    # Return claude:auto label
    mock_provider.client._query = AsyncMock(return_value={
        "issue": {"labels": {"nodes": [{"id": "lbl-1", "name": "claude:auto"}]}}
    })

    payload = _make_state_change_payload()
    await event_source.handle_webhook(payload)

    assert event_source._handler.called


@pytest.mark.asyncio
async def test_multiple_labels_including_auto_passes(event_source, mock_provider):
    """Issue with multiple labels including claude:auto should pass."""
    api_issue = MagicMock()
    api_issue.assignee = MagicMock(id="user-bob")
    mock_provider.client.get_issue = AsyncMock(return_value=api_issue)

    mock_provider.client._query = AsyncMock(return_value={
        "issue": {"labels": {"nodes": [
            {"id": "lbl-1", "name": "repo:api"},
            {"id": "lbl-2", "name": "claude:auto"},
            {"id": "lbl-3", "name": "priority:high"},
        ]}}
    })

    payload = _make_state_change_payload()
    await event_source.handle_webhook(payload)

    assert event_source._handler.called


# -- Drops: missing label --


@pytest.mark.asyncio
async def test_missing_auto_label_drops(event_source, mock_provider):
    """Issue with allowed assignee but no claude:auto label should be dropped."""
    api_issue = MagicMock()
    api_issue.assignee = MagicMock(id="user-alice")
    mock_provider.client.get_issue = AsyncMock(return_value=api_issue)

    mock_provider.client._query = AsyncMock(return_value={
        "issue": {"labels": {"nodes": [{"id": "lbl-1", "name": "repo:api"}]}}
    })

    payload = _make_state_change_payload()
    await event_source.handle_webhook(payload)

    assert not event_source._handler.called


# -- Drops: wrong assignee --


@pytest.mark.asyncio
async def test_wrong_assignee_drops(event_source, mock_provider):
    """Issue assigned to a user not in the allowlist should be dropped."""
    api_issue = MagicMock()
    api_issue.assignee = MagicMock(id="user-charlie")
    mock_provider.client.get_issue = AsyncMock(return_value=api_issue)

    payload = _make_state_change_payload()
    await event_source.handle_webhook(payload)

    # Should not even check labels - dropped at assignee check
    assert not event_source._handler.called


# -- Drops: unassigned --


@pytest.mark.asyncio
async def test_unassigned_drops(event_source, mock_provider):
    """Issue with no assignee should be dropped."""
    api_issue = MagicMock()
    api_issue.assignee = None
    mock_provider.client.get_issue = AsyncMock(return_value=api_issue)

    payload = _make_state_change_payload()
    await event_source.handle_webhook(payload)

    assert not event_source._handler.called


# -- Drops: both conditions fail --


@pytest.mark.asyncio
async def test_wrong_assignee_and_no_label_drops(event_source, mock_provider):
    """Issue with wrong assignee and no auto label should be dropped."""
    api_issue = MagicMock()
    api_issue.assignee = MagicMock(id="user-charlie")
    mock_provider.client.get_issue = AsyncMock(return_value=api_issue)

    payload = _make_state_change_payload()
    await event_source.handle_webhook(payload)

    assert not event_source._handler.called


# -- Comment webhook bypasses filter --


@pytest.mark.asyncio
async def test_comment_webhook_bypasses_filter(event_source, mock_provider):
    """Comment webhooks should not go through intake filter."""
    payload = _make_comment_payload()
    await event_source.handle_webhook(payload)

    # Comment handler dispatches event without calling get_issue for filter
    assert event_source._handler.called
    mock_provider.client.get_issue.assert_not_called()


# -- Edge cases --


@pytest.mark.asyncio
async def test_non_state_change_update_bypasses_filter(event_source, mock_provider):
    """An update that is NOT a state change should bypass the intake filter."""
    data = make_issue_data()
    payload = WebhookPayload(
        action="update",
        type="Issue",
        data=data,
        updatedFrom={"title": "Old title"},  # Not a stateId change
        createdAt=datetime.now(),
    )

    await event_source.handle_webhook(payload)

    # Should dispatch without checking intake filter
    assert event_source._handler.called
    mock_provider.client.get_issue.assert_not_called()


@pytest.mark.asyncio
async def test_api_failure_drops_gracefully(event_source, mock_provider):
    """If the API call to fetch the issue fails, drop the event gracefully."""
    mock_provider.client.get_issue = AsyncMock(side_effect=Exception("API timeout"))

    payload = _make_state_change_payload()
    await event_source.handle_webhook(payload)

    assert not event_source._handler.called


@pytest.mark.asyncio
async def test_empty_allowed_assignees_passes_all(mock_provider, instance):
    """When allowed_assignees is empty, any assignee should pass."""
    source = LinearWebhookEventSource(
        provider=mock_provider,
        instance=instance,
        allowed_assignees=[],
    )
    source._handler = AsyncMock()
    source._bot_user_id = "bot-user-id"

    api_issue = MagicMock()
    api_issue.assignee = MagicMock(id="user-anyone")
    mock_provider.client.get_issue = AsyncMock(return_value=api_issue)

    mock_provider.client._query = AsyncMock(return_value={
        "issue": {"labels": {"nodes": [{"id": "lbl-1", "name": "claude:auto"}]}}
    })

    payload = _make_state_change_payload()
    await source.handle_webhook(payload)

    assert source._handler.called
