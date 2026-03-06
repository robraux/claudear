"""Tests for REPO_MAP and config parsing."""

from __future__ import annotations

import json

import pytest

from claudear.core.config import MultiProviderSettings


def test_repo_map_valid_json(sample_repo_map):
    settings = MultiProviderSettings(repo_map=json.dumps(sample_repo_map))
    result = settings.get_repo_map()
    assert "api" in result
    assert "web" in result
    assert result["api"].exists()


def test_repo_map_invalid_json():
    settings = MultiProviderSettings(repo_map="not json")
    result = settings.get_repo_map()
    assert result == {}


def test_repo_map_missing_path(tmp_path):
    repo_map = {"api": str(tmp_path / "nonexistent")}
    settings = MultiProviderSettings(repo_map=json.dumps(repo_map))
    result = settings.get_repo_map()
    assert result == {}


def test_repo_map_not_git_repo(tmp_path):
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    repo_map = {"api": str(plain_dir)}
    settings = MultiProviderSettings(repo_map=json.dumps(repo_map))
    result = settings.get_repo_map()
    assert result == {}


def test_repo_map_empty_string():
    settings = MultiProviderSettings(repo_map="")
    result = settings.get_repo_map()
    assert result == {}


def test_repo_map_none():
    settings = MultiProviderSettings(repo_map=None)
    result = settings.get_repo_map()
    assert result == {}


def test_repo_map_mixed_valid_invalid(sample_repo_map, tmp_path):
    combined = {**sample_repo_map, "bad": str(tmp_path / "missing")}
    settings = MultiProviderSettings(repo_map=json.dumps(combined))
    result = settings.get_repo_map()
    assert "api" in result
    assert "web" in result
    assert "bad" not in result


def test_allowed_assignees_parsing():
    settings = MultiProviderSettings(allowed_assignees="user-a, user-b, user-c")
    result = settings.get_allowed_assignees()
    assert result == ["user-a", "user-b", "user-c"]


def test_allowed_assignees_empty():
    settings = MultiProviderSettings(allowed_assignees="")
    assert settings.get_allowed_assignees() == []


def test_validate_config_requires_repo_map():
    settings = MultiProviderSettings(
        linear_api_key="key",
        linear_team_id="ENG",
        linear_webhook_secret="secret",
        github_token="gh-token",
    )
    errors = settings.validate_config()
    assert any("REPO_MAP" in e for e in errors)


def test_validate_config_requires_allowed_assignees(sample_repo_map):
    settings = MultiProviderSettings(
        linear_api_key="key",
        linear_team_id="ENG",
        linear_webhook_secret="secret",
        github_token="gh-token",
        repo_map=json.dumps(sample_repo_map),
    )
    errors = settings.validate_config()
    assert any("ALLOWED_ASSIGNEES" in e for e in errors)


def test_validate_config_passes_with_all_required(sample_repo_map):
    settings = MultiProviderSettings(
        linear_api_key="key",
        linear_team_id="ENG",
        linear_webhook_secret="secret",
        github_token="gh-token",
        repo_map=json.dumps(sample_repo_map),
        allowed_assignees="user-a",
    )
    errors = settings.validate_config()
    assert errors == []
