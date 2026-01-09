"""Workspace configuration - re-exports from main config module.

This module is kept for backward compatibility. All config types and functions
have been moved to pochi.config.
"""

from __future__ import annotations

# Re-export everything from the main config module
from ..config import (
    ConfigError,
    FolderConfig,
    RalphConfig,
    TelegramConfig,
    WorkspaceConfig,
    add_folder_to_workspace,
    create_workspace,
    find_workspace_root,
    load_workspace_config,
    save_workspace_config,
    update_folder_topic_id,
    WORKSPACE_CONFIG_DIR,
    WORKSPACE_CONFIG_FILE,
)


__all__ = [
    "ConfigError",
    "FolderConfig",
    "RalphConfig",
    "TelegramConfig",
    "WorkspaceConfig",
    "add_folder_to_workspace",
    "create_workspace",
    "find_workspace_root",
    "load_workspace_config",
    "save_workspace_config",
    "update_folder_topic_id",
    "WORKSPACE_CONFIG_DIR",
    "WORKSPACE_CONFIG_FILE",
]
