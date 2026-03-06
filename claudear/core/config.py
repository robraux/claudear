"""Configuration for Claudear."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

from claudear.core.types import ProviderType, TaskStatus, ProviderInstance

logger = logging.getLogger(__name__)


def _find_env_file() -> Optional[str]:
    """Find .env file in current working directory."""
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        return str(env_path)
    return None


class MultiProviderSettings(BaseSettings):
    """Settings for Claudear task automation."""

    model_config = SettingsConfigDict(
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # Linear Configuration
    # -------------------------------------------------------------------------

    linear_api_key: Optional[str] = None
    linear_webhook_secret: Optional[str] = None

    # Multi-team: comma-separated team keys (e.g., "ENG,INFRA,DESIGN")
    linear_team_ids: Optional[str] = None
    # Single-team (backward compat)
    linear_team_id: Optional[str] = None

    # Linear labels
    linear_labels_enabled: bool = True
    linear_labels_activity_enabled: bool = True
    linear_labels_debounce_seconds: float = 2.0

    # -------------------------------------------------------------------------
    # Repo Routing
    # -------------------------------------------------------------------------

    # JSON mapping of repo key -> local path, e.g.:
    # {"api": "/home/user/repos/api", "web": "/home/user/repos/web"}
    repo_map: Optional[str] = None

    # -------------------------------------------------------------------------
    # Intake Filtering
    # -------------------------------------------------------------------------

    # Comma-separated Linear user IDs allowed to trigger automation
    allowed_assignees: Optional[str] = None

    # -------------------------------------------------------------------------
    # Two-Phase Pipeline
    # -------------------------------------------------------------------------

    # Phase 1: Spec generation
    phase1_trigger_state: str = "Ready for Spec"
    phase1_active_state: str = "Spec in Progress"
    phase1_complete_state: str = "Spec Review"
    phase1_command: str = "generate-spec"

    # Phase 2: Implementation
    phase2_trigger_state: str = "Ready for Dev"
    phase2_active_state: str = "Dev in Progress"
    phase2_complete_state: str = "In Review"
    phase2_command: str = "implement-spec"

    # Shared
    blocked_state: str = "Blocked"

    # -------------------------------------------------------------------------
    # Shared Configuration
    # -------------------------------------------------------------------------

    github_token: Optional[str] = None

    # Server
    webhook_port: int = 8741
    webhook_host: str = "0.0.0.0"
    ngrok_authtoken: Optional[str] = None

    # Task settings
    max_concurrent_tasks: int = 5
    comment_poll_interval: int = 30  # seconds
    blocked_timeout: int = 3600  # seconds

    # Logging
    log_level: str = "INFO"

    # Database
    db_path: str = "claudear.db"

    # -------------------------------------------------------------------------
    # Derived Properties
    # -------------------------------------------------------------------------

    def get_repo_map(self) -> dict[str, Path]:
        """Parse REPO_MAP JSON into a dict of repo key -> Path.

        Returns:
            Dict mapping repo keys to validated local paths.
            Invalid paths are excluded with a warning.
        """
        if not self.repo_map:
            return {}

        try:
            raw = json.loads(self.repo_map)
        except json.JSONDecodeError as e:
            logger.error(f"REPO_MAP is not valid JSON: {e}")
            return {}

        if not isinstance(raw, dict):
            logger.error("REPO_MAP must be a JSON object")
            return {}

        result: dict[str, Path] = {}
        for key, path_str in raw.items():
            path = Path(path_str)
            if not path.exists():
                logger.warning(f"REPO_MAP: path for '{key}' does not exist: {path}")
                continue
            if not (path / ".git").exists():
                logger.warning(f"REPO_MAP: path for '{key}' is not a git repo: {path}")
                continue
            result[key] = path

        return result

    def get_allowed_assignees(self) -> list[str]:
        """Parse ALLOWED_ASSIGNEES into a list of user IDs."""
        if not self.allowed_assignees:
            return []
        return [a.strip() for a in self.allowed_assignees.split(",") if a.strip()]

    def get_linear_team_ids(self) -> list[str]:
        """Get list of Linear team IDs/keys to manage."""
        if self.linear_team_ids:
            return [t.strip() for t in self.linear_team_ids.split(",") if t.strip()]
        elif self.linear_team_id:
            return [self.linear_team_id]
        return []

    def get_linear_instance(self, team_id: str) -> Optional[ProviderInstance]:
        """Create a ProviderInstance for a Linear team.

        Note: repo_path on ProviderInstance is no longer used for routing.
        Repo routing is done via labels + REPO_MAP at task creation time.
        """
        # Use a dummy path - actual routing goes through REPO_MAP
        repo_map = self.get_repo_map()
        # Pick the first repo path as a fallback for ProviderInstance
        # (this field will be removed in a future cleanup)
        fallback_path = next(iter(repo_map.values()), None) if repo_map else None
        if not fallback_path:
            logger.warning(f"No repos in REPO_MAP for Linear team {team_id}")
            return None

        status_mapping = self._get_status_mapping(team_id)

        return ProviderInstance(
            provider=ProviderType.LINEAR,
            instance_id=team_id,
            display_name=f"Linear/{team_id}",
            repo_path=fallback_path,
            status_todo=status_mapping.get(TaskStatus.TODO),
            status_in_progress=status_mapping.get(TaskStatus.IN_PROGRESS),
            status_in_review=status_mapping.get(TaskStatus.IN_REVIEW),
            status_done=status_mapping.get(TaskStatus.DONE),
        )

    def _get_status_mapping(self, instance_id: str) -> dict[TaskStatus, str]:
        """Get custom status mappings for a Linear team instance."""
        safe_id = instance_id.replace("-", "_").upper()
        prefix = f"LINEAR_{safe_id}_STATE_"

        mapping = {}
        status_map = {
            "TODO": TaskStatus.TODO,
            "IN_PROGRESS": TaskStatus.IN_PROGRESS,
            "IN_REVIEW": TaskStatus.IN_REVIEW,
            "DONE": TaskStatus.DONE,
        }

        for status_name, status_enum in status_map.items():
            env_var = f"{prefix}{status_name}"
            value = os.environ.get(env_var)
            if value:
                mapping[status_enum] = value

        return mapping

    def has_linear(self) -> bool:
        """Check if Linear is configured."""
        return bool(self.linear_api_key and self.get_linear_team_ids())

    def validate_config(self) -> list[str]:
        """Validate the configuration.

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        if not self.has_linear():
            errors.append(
                "No providers configured. Set LINEAR_API_KEY + LINEAR_TEAM_ID"
            )

        if self.linear_api_key:
            if not self.get_linear_team_ids():
                errors.append(
                    "LINEAR_API_KEY set but no teams configured. "
                    "Set LINEAR_TEAM_ID or LINEAR_TEAM_IDS"
                )
            if not self.linear_webhook_secret:
                errors.append(
                    "LINEAR_API_KEY set but LINEAR_WEBHOOK_SECRET missing"
                )

        # REPO_MAP is required
        if not self.repo_map:
            errors.append("REPO_MAP not set")
        else:
            repo_map = self.get_repo_map()
            if not repo_map:
                errors.append("REPO_MAP has no valid entries")

        # ALLOWED_ASSIGNEES is required
        if not self.get_allowed_assignees():
            errors.append("ALLOWED_ASSIGNEES not set or empty")

        if not self.github_token:
            errors.append("GITHUB_TOKEN not set")

        return errors


# Global settings instance
_settings: Optional[MultiProviderSettings] = None


def get_settings() -> MultiProviderSettings:
    """Get the application settings."""
    global _settings
    if _settings is None:
        _settings = MultiProviderSettings()
    return _settings


def reload_settings() -> MultiProviderSettings:
    """Force reload settings from environment."""
    global _settings
    _settings = MultiProviderSettings()
    return _settings
