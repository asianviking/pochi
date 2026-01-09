"""Pydantic settings for workspace configuration.

This module provides:
- Environment variable support (POCHI__TELEGRAM__BOT_TOKEN, etc.)
- Validation with clear error messages
- SecretStr for bot token to prevent accidental logging
- Type coercion built-in
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .config_store import (
    WORKSPACE_CONFIG_DIR,
    WORKSPACE_CONFIG_FILE,
    get_config_path,
    read_raw_toml,
)
from .logging import get_logger
from .transport import ChannelId

logger = get_logger(__name__)


class ConfigError(RuntimeError):
    """Configuration error."""

    pass


class RalphSettings(BaseModel):
    """Ralph Wiggum loop configuration."""

    enabled: bool = False
    default_max_iterations: int = 3


class FolderSettings(BaseModel):
    """Configuration for a folder in the workspace."""

    path: str
    channels: list[ChannelId] = []
    topic_id: int | None = None
    description: str | None = None
    origin: str | None = None
    pending_topic: bool = False


class TelegramSettings(BaseModel):
    """Telegram transport configuration."""

    bot_token: SecretStr
    chat_id: int


class WorkspaceSettings(BaseSettings):
    """Workspace configuration loaded from TOML and environment variables.

    Environment variables use POCHI__ prefix with __ as nested delimiter:
    - POCHI__NAME -> name
    - POCHI__TELEGRAM__BOT_TOKEN -> telegram.bot_token
    - POCHI__TELEGRAM__CHAT_ID -> telegram.chat_id
    """

    model_config = SettingsConfigDict(
        env_prefix="POCHI__",
        env_nested_delimiter="__",
        extra="allow",  # Allow engine-specific sections like [claude], [codex]
    )

    # Core workspace settings
    name: str = ""
    default_engine: str = "claude"
    message_batch_window_ms: float = 200.0

    # Transport config
    telegram: TelegramSettings | None = None

    # Folders config (dict keyed by folder name)
    folders: dict[str, FolderSettings] = {}

    # Ralph config (nested under [workers.ralph] in TOML)
    ralph: RalphSettings = RalphSettings()

    # Legacy fields - will be migrated to [telegram] section
    telegram_group_id: int | None = None
    bot_token: SecretStr | None = None

    @model_validator(mode="after")
    def _migrate_legacy_telegram(self) -> "WorkspaceSettings":
        """Populate telegram section from legacy fields if not set."""
        if self.telegram is None and self.bot_token and self.telegram_group_id:
            self.telegram = TelegramSettings(
                bot_token=self.bot_token,
                chat_id=self.telegram_group_id,
            )
        return self


def find_workspace_root(start_path: Path | None = None) -> Path | None:
    """Walk up from start_path to find a workspace root.

    A workspace root is a directory containing .pochi/workspace.toml.
    """
    if start_path is None:
        start_path = Path.cwd()

    current = start_path.resolve()
    while current != current.parent:
        config_path = current / WORKSPACE_CONFIG_DIR / WORKSPACE_CONFIG_FILE
        if config_path.exists():
            return current
        current = current.parent

    # Check root as well
    config_path = current / WORKSPACE_CONFIG_DIR / WORKSPACE_CONFIG_FILE
    if config_path.exists():
        return current

    return None


def _parse_folders(data: dict[str, Any]) -> dict[str, FolderSettings]:
    """Parse folders from raw TOML data with legacy [repos] migration."""
    folders: dict[str, FolderSettings] = {}

    # Check for folders section, fall back to legacy repos
    folders_data = data.get("folders", {})
    if not folders_data and "repos" in data:
        folders_data = data.get("repos", {})

    for name, folder_data in folders_data.items():
        if not isinstance(folder_data, dict):
            continue
        folders[name] = FolderSettings(
            path=folder_data.get("path", name),
            channels=folder_data.get("channels", []),
            topic_id=folder_data.get("topic_id"),
            description=folder_data.get("description"),
            origin=folder_data.get("origin"),
            pending_topic=folder_data.get("pending_topic", False),
        )

    return folders


def _parse_ralph(data: dict[str, Any]) -> RalphSettings:
    """Parse ralph config from raw TOML data."""
    ralph_data = data.get("workers", {}).get("ralph", {})
    return RalphSettings(
        enabled=ralph_data.get("enabled", False),
        default_max_iterations=ralph_data.get("default_max_iterations", 3),
    )


def _parse_telegram(data: dict[str, Any]) -> TelegramSettings | None:
    """Parse telegram config from raw TOML data."""
    telegram_data = data.get("telegram", {})
    if telegram_data and "bot_token" in telegram_data and "chat_id" in telegram_data:
        return TelegramSettings(
            bot_token=SecretStr(telegram_data["bot_token"]),
            chat_id=telegram_data["chat_id"],
        )
    return None


def load_settings(workspace_root: Path | None = None) -> WorkspaceSettings | None:
    """Load workspace settings from TOML file.

    Args:
        workspace_root: Path to workspace root. If None, will search for it.

    Returns:
        WorkspaceSettings if config exists and is valid, None otherwise.
    """
    if workspace_root is None:
        workspace_root = find_workspace_root()
        if workspace_root is None:
            return None

    config_path = get_config_path(workspace_root)
    if not config_path.exists():
        return None

    try:
        data = read_raw_toml(config_path)
    except Exception as e:
        logger.error(
            "settings.load_failed",
            path=str(config_path),
            error=str(e),
        )
        return None

    # Parse workspace section
    workspace_data = data.get("workspace", {})

    # Parse legacy fields from workspace section
    legacy_bot_token = workspace_data.get("bot_token")
    legacy_group_id = workspace_data.get("telegram_group_id")

    # Build settings with parsed components
    try:
        settings = WorkspaceSettings(
            name=workspace_data.get("name", workspace_root.name),
            default_engine=workspace_data.get("default_engine", "claude"),
            message_batch_window_ms=workspace_data.get("message_batch_window_ms", 200.0),
            telegram=_parse_telegram(data),
            folders=_parse_folders(data),
            ralph=_parse_ralph(data),
            telegram_group_id=legacy_group_id,
            bot_token=SecretStr(legacy_bot_token) if legacy_bot_token else None,
        )
        return settings
    except Exception as e:
        logger.error(
            "settings.validation_failed",
            path=str(config_path),
            error=str(e),
        )
        return None
