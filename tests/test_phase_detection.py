"""Tests for two-phase pipeline state detection in LinearWebhookEventSource."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from claudear.core.types import ProviderType, ProviderInstance, TaskStatus
from claudear.linear.models import WebhookPayload
from claudear.providers.linear.webhook import LinearWebhookEventSource

from tests.factories import make_issue_data


PHASE_CONFIG = {
    "Ready for Spec": {"phase": "spec", "command": "generate-spec"},
    "Ready for Dev": {"phase": "implement", "command": "implement-spec"},
}


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
    provider.detect_status = AsyncMock(return_value=TaskStatus.TODO)
    # _get_state_info returns (name, type) tuples
    provider._get_state_info = AsyncMock(return_value=None)
    return provider


@pytest.fixture
def event_source(mock_provider, instance):
    source = LinearWebhookEventSource(
        provider=mock_provider,
        instance=instance,
        allowed_assignees=["user-alice"],
        valid_repo_keys=["api", "web"],
        phase_config=PHASE_CONFIG,
    )
    source._handler = AsyncMock()
    source._bot_user_id = "bot-user-id"
    return source


def _setup_intake_pass(mock_provider):
    """Set up mocks so the intake filter passes."""
    api_issue = MagicMock()
    api_issue.assignee = MagicMock(id="user-alice")
    mock_provider.client.get_issue = AsyncMock(return_value=api_issue)
    # Labels: claude:auto + repo:api
    mock_provider.client._query = AsyncMock(return_value={
        "issue": {"labels": {"nodes": [
            {"id": "lbl-1", "name": "claude:auto"},
            {"id": "lbl-2", "name": "repo:api"},
        ]}}
    })


def _make_state_change_payload(new_state_id="state-new", old_state_id="state-old"):
    data = make_issue_data(state_id=new_state_id)
    return WebhookPayload(
        action="update",
        type="Issue",
        data=data,
        updatedFrom={"stateId": old_state_id},
        createdAt=datetime.now(),
    )


# -- Phase 1 trigger --


@pytest.mark.asyncio
async def test_ready_for_spec_emits_phase1(event_source, mock_provider):
    """Webhook with 'Ready for Spec' state emits Phase 1 event."""
    _setup_intake_pass(mock_provider)
    mock_provider._get_state_info = AsyncMock(return_value=("Ready for Spec", "unstarted"))

    payload = _make_state_change_payload()
    await event_source.handle_webhook(payload)

    assert event_source._handler.called
    event = event_source._handler.call_args[0][0]
    assert event.phase == "spec"
    assert event.phase_command == "generate-spec"
    assert event.is_phase_trigger()
    assert event.repo_keys == ["api"]


# -- Phase 2 trigger --


@pytest.mark.asyncio
async def test_ready_for_dev_emits_phase2(event_source, mock_provider):
    """Webhook with 'Ready for Dev' state emits Phase 2 event."""
    _setup_intake_pass(mock_provider)
    mock_provider._get_state_info = AsyncMock(return_value=("Ready for Dev", "started"))

    payload = _make_state_change_payload()
    await event_source.handle_webhook(payload)

    assert event_source._handler.called
    event = event_source._handler.call_args[0][0]
    assert event.phase == "implement"
    assert event.phase_command == "implement-spec"
    assert event.is_phase_trigger()


# -- Unrelated state is not a phase trigger --


@pytest.mark.asyncio
async def test_unrelated_state_not_phase_trigger(event_source, mock_provider):
    """Webhook with a non-trigger state does not emit phase info."""
    _setup_intake_pass(mock_provider)
    mock_provider._get_state_info = AsyncMock(return_value=("In Review", "started"))

    payload = _make_state_change_payload()
    await event_source.handle_webhook(payload)

    assert event_source._handler.called
    event = event_source._handler.call_args[0][0]
    assert event.phase is None
    assert event.phase_command is None
    assert not event.is_phase_trigger()
    assert event.repo_keys == []


# -- Custom trigger state names work --


@pytest.mark.asyncio
async def test_custom_trigger_state_names(mock_provider, instance):
    """Custom phase trigger state names work correctly."""
    custom_config = {
        "Awaiting Spec": {"phase": "spec", "command": "my-spec-cmd"},
        "Implement Now": {"phase": "implement", "command": "my-impl-cmd"},
    }

    source = LinearWebhookEventSource(
        provider=mock_provider,
        instance=instance,
        allowed_assignees=["user-alice"],
        valid_repo_keys=["api"],
        phase_config=custom_config,
    )
    source._handler = AsyncMock()
    source._bot_user_id = "bot-user-id"

    _setup_intake_pass(mock_provider)
    mock_provider._get_state_info = AsyncMock(return_value=("Awaiting Spec", "unstarted"))

    payload = _make_state_change_payload()
    await source.handle_webhook(payload)

    event = source._handler.call_args[0][0]
    assert event.phase == "spec"
    assert event.phase_command == "my-spec-cmd"


# -- Phase trigger includes state name --


@pytest.mark.asyncio
async def test_event_includes_state_name(event_source, mock_provider):
    """Event should include the exact Linear state name."""
    _setup_intake_pass(mock_provider)
    mock_provider._get_state_info = AsyncMock(return_value=("Ready for Spec", "unstarted"))

    payload = _make_state_change_payload()
    await event_source.handle_webhook(payload)

    event = event_source._handler.call_args[0][0]
    assert event.new_state_name == "Ready for Spec"
