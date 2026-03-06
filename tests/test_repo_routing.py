"""Tests for label-based repo routing in LinearWebhookEventSource."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from claudear.core.types import ProviderType, ProviderInstance
from claudear.providers.linear.webhook import LinearWebhookEventSource


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
    return provider


@pytest.fixture
def event_source(mock_provider, instance):
    return LinearWebhookEventSource(
        provider=mock_provider,
        instance=instance,
        valid_repo_keys=["api", "web", "infra"],
    )


# -- Single repo label --


@pytest.mark.asyncio
async def test_single_repo_label(event_source, mock_provider):
    """Issue with one repo:X label returns that key."""
    mock_provider.client._query = AsyncMock(return_value={
        "issue": {"labels": {"nodes": [
            {"id": "lbl-1", "name": "repo:api"},
            {"id": "lbl-2", "name": "claude:auto"},
        ]}}
    })

    keys = await event_source.resolve_repo_labels("issue-1", "ENG-1")
    assert keys == ["api"]


# -- Multiple repo labels --


@pytest.mark.asyncio
async def test_multiple_repo_labels(event_source, mock_provider):
    """Issue with multiple repo:X labels returns all valid keys."""
    mock_provider.client._query = AsyncMock(return_value={
        "issue": {"labels": {"nodes": [
            {"id": "lbl-1", "name": "repo:api"},
            {"id": "lbl-2", "name": "repo:web"},
            {"id": "lbl-3", "name": "claude:auto"},
        ]}}
    })

    keys = await event_source.resolve_repo_labels("issue-1", "ENG-1")
    assert keys == ["api", "web"]


# -- Unknown key skipped --


@pytest.mark.asyncio
async def test_unknown_repo_key_skipped(event_source, mock_provider):
    """Unknown repo keys are skipped with a warning."""
    mock_provider.client._query = AsyncMock(return_value={
        "issue": {"labels": {"nodes": [
            {"id": "lbl-1", "name": "repo:api"},
            {"id": "lbl-2", "name": "repo:unknown-service"},
        ]}}
    })

    keys = await event_source.resolve_repo_labels("issue-1", "ENG-1")
    assert keys == ["api"]
    assert "unknown-service" not in keys


# -- No repo labels returns empty --


@pytest.mark.asyncio
async def test_no_repo_labels_returns_empty(event_source, mock_provider):
    """Issue with no repo:X labels returns an empty list."""
    mock_provider.client._query = AsyncMock(return_value={
        "issue": {"labels": {"nodes": [
            {"id": "lbl-1", "name": "claude:auto"},
            {"id": "lbl-2", "name": "priority:high"},
        ]}}
    })

    keys = await event_source.resolve_repo_labels("issue-1", "ENG-1")
    assert keys == []


# -- Mixed valid and invalid keys --


@pytest.mark.asyncio
async def test_mixed_valid_and_invalid_keys(event_source, mock_provider):
    """Only valid repo keys are returned, invalid ones skipped."""
    mock_provider.client._query = AsyncMock(return_value={
        "issue": {"labels": {"nodes": [
            {"id": "lbl-1", "name": "repo:api"},
            {"id": "lbl-2", "name": "repo:badkey"},
            {"id": "lbl-3", "name": "repo:infra"},
            {"id": "lbl-4", "name": "repo:nope"},
        ]}}
    })

    keys = await event_source.resolve_repo_labels("issue-1", "ENG-1")
    assert keys == ["api", "infra"]


# -- No valid_repo_keys means all keys pass --


@pytest.mark.asyncio
async def test_no_valid_repo_keys_passes_all(mock_provider, instance):
    """When valid_repo_keys is empty, all repo:X labels pass through."""
    source = LinearWebhookEventSource(
        provider=mock_provider,
        instance=instance,
        valid_repo_keys=[],
    )

    mock_provider.client._query = AsyncMock(return_value={
        "issue": {"labels": {"nodes": [
            {"id": "lbl-1", "name": "repo:anything"},
            {"id": "lbl-2", "name": "repo:whatever"},
        ]}}
    })

    keys = await source.resolve_repo_labels("issue-1", "ENG-1")
    assert keys == ["anything", "whatever"]


# -- API failure returns empty --


@pytest.mark.asyncio
async def test_api_failure_returns_empty(event_source, mock_provider):
    """If label fetch fails, return empty list gracefully."""
    mock_provider.client._query = AsyncMock(side_effect=Exception("API error"))

    keys = await event_source.resolve_repo_labels("issue-1", "ENG-1")
    assert keys == []
