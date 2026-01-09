"""Config migrations system for Pochi.

This module provides a structured way to migrate workspace configuration
files as the schema evolves. Each migration is a function that transforms
the raw TOML data and returns True if it made changes.

Migrations are applied automatically at startup before config validation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config_store import read_raw_toml, write_raw_toml, backup_config
from .logging import get_logger

logger = get_logger(__name__)


def migrate_config(config: dict[str, Any], *, config_path: Path) -> list[str]:
    """Apply all applicable migrations to a config dict.

    Args:
        config: Raw config dict (modified in-place)
        config_path: Path to the config file (for logging)

    Returns:
        List of applied migration names
    """
    applied: list[str] = []

    if _migrate_repos_to_folders(config):
        applied.append("repos-to-folders")

    if _migrate_legacy_telegram(config):
        applied.append("legacy-telegram")

    return applied


def migrate_config_file(path: Path) -> list[str]:
    """Load config, apply migrations, save if changed.

    Args:
        path: Path to the workspace.toml file

    Returns:
        List of applied migration names
    """
    if not path.exists():
        return []

    try:
        config = read_raw_toml(path)
    except Exception as e:
        logger.warning(
            "config.migration.read_failed",
            path=str(path),
            error=str(e),
        )
        return []

    applied = migrate_config(config, config_path=path)

    if applied:
        # Create backup before modifying
        backup_path = backup_config(path)
        if backup_path:
            logger.info(
                "config.migration.backup_created",
                path=str(path),
                backup=str(backup_path),
            )

        try:
            write_raw_toml(config, path)
        except Exception as e:
            logger.error(
                "config.migration.write_failed",
                path=str(path),
                error=str(e),
            )
            return []

        for migration in applied:
            logger.info(
                "config.migrated",
                migration=migration,
                path=str(path),
            )

    return applied


def _ensure_table(
    config: dict[str, Any], key: str, *, config_path: Path
) -> dict[str, Any]:
    """Ensure a table exists in config, creating it if needed."""
    if key not in config:
        config[key] = {}
    return config[key]


def _migrate_repos_to_folders(config: dict[str, Any]) -> bool:
    """Migrate [repos.*] section to [folders.*].

    This migration handles the rename from 'repos' to 'folders' that was
    introduced to support non-git directories.

    Returns:
        True if migration was applied
    """
    if "repos" not in config:
        return False

    # Don't migrate if folders already exists
    if "folders" in config:
        # Just remove the old repos section
        del config["repos"]
        return True

    # Move repos to folders
    config["folders"] = config.pop("repos")
    return True


def _migrate_legacy_telegram(config: dict[str, Any]) -> bool:
    """Migrate legacy telegram fields from [workspace] to [telegram].

    Old format:
        [workspace]
        bot_token = "..."
        telegram_group_id = 123456

    New format:
        [telegram]
        bot_token = "..."
        chat_id = 123456

    Returns:
        True if migration was applied
    """
    workspace = config.get("workspace", {})

    # Check for legacy fields in workspace section
    has_legacy_bot_token = "bot_token" in workspace
    has_legacy_group_id = "telegram_group_id" in workspace

    if not has_legacy_bot_token and not has_legacy_group_id:
        return False

    # Don't migrate if [telegram] section already has these fields
    telegram = config.get("telegram", {})
    if "bot_token" in telegram and "chat_id" in telegram:
        # Just remove legacy fields
        workspace.pop("bot_token", None)
        workspace.pop("telegram_group_id", None)
        return True

    # Create or update [telegram] section
    if "telegram" not in config:
        config["telegram"] = {}

    telegram = config["telegram"]

    # Move bot_token if present and not already in telegram
    if has_legacy_bot_token and "bot_token" not in telegram:
        telegram["bot_token"] = workspace.pop("bot_token")

    # Move telegram_group_id as chat_id
    if has_legacy_group_id and "chat_id" not in telegram:
        telegram["chat_id"] = workspace.pop("telegram_group_id")

    return True
