"""Workspace configuration loading and management.

This module provides the runtime configuration model (WorkspaceConfig) and
conversion from pydantic settings. The dataclass-based WorkspaceConfig is
kept for backward compatibility with existing code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomlkit

from .config_store import (
    WORKSPACE_CONFIG_DIR,
    WORKSPACE_CONFIG_FILE,
)
from .logging import get_logger
from .settings import (
    ConfigError,
    WorkspaceSettings,
    find_workspace_root,
    load_settings,
)
from .transport import ChannelId

logger = get_logger(__name__)

# Re-export for backward compatibility
__all__ = [
    "ConfigError",
    "FolderConfig",
    "PluginsConfig",
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


@dataclass
class PluginsConfig:
    """Plugin configuration."""

    # List of distribution names to enable. Empty = load all.
    enabled: list[str] = field(default_factory=list)

    # Reserved for future use - not implemented
    auto_install: bool = False


@dataclass
class RalphConfig:
    """Ralph Wiggum loop configuration."""

    enabled: bool = False
    default_max_iterations: int = 3


@dataclass
class FolderConfig:
    """Configuration for a folder in the workspace (repo or plain directory)."""

    name: str
    path: str  # Relative to workspace root
    channels: list[ChannelId] = field(default_factory=list)
    topic_id: int | None = None
    description: str | None = None
    origin: str | None = None
    pending_topic: bool = False

    def absolute_path(self, workspace_root: Path) -> Path:
        """Get the absolute path to this folder."""
        return workspace_root / self.path

    def is_git_repo(self, workspace_root: Path) -> bool:
        """Check if this folder is a git repository."""
        git_dir = self.absolute_path(workspace_root) / ".git"
        return git_dir.exists()


@dataclass
class TelegramConfig:
    """Telegram transport configuration."""

    bot_token: str
    chat_id: int


@dataclass
class WorkspaceConfig:
    """Configuration for a workspace with multiple folders."""

    name: str
    root: Path  # Absolute path to workspace root
    folders: dict[str, FolderConfig] = field(default_factory=dict)
    ralph: RalphConfig = field(default_factory=RalphConfig)
    default_engine: str = "claude"
    default_transport: str = "telegram"

    # Worktree settings
    worktrees_dir: str = ".worktrees"  # Directory name for worktrees within folders
    worktree_base: str | None = (
        None  # Base branch for new worktrees (auto-detect if None)
    )

    # Transport configs (new format: [transports.<id>] sections)
    transports: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Legacy transport config
    telegram: TelegramConfig | None = None

    # Plugin configuration
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
    plugin_configs: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Legacy fields for backwards compatibility
    telegram_group_id: int = 0
    bot_token: str = ""

    def get_folder_by_topic(self, topic_id: int) -> FolderConfig | None:
        """Find a folder by its Telegram topic ID."""
        for folder in self.folders.values():
            if folder.topic_id == topic_id:
                return folder
        return None

    def get_folder_by_channel(self, channel_id: ChannelId) -> FolderConfig | None:
        """Find a folder by any of its channel IDs."""
        for folder in self.folders.values():
            if channel_id in folder.channels:
                return folder
        return None

    def get_pending_topics(self) -> list[FolderConfig]:
        """Get all folders that need topics created."""
        return [folder for folder in self.folders.values() if folder.pending_topic]

    def config_path(self) -> Path:
        """Get the path to the workspace config file."""
        return self.root / WORKSPACE_CONFIG_DIR / WORKSPACE_CONFIG_FILE

    def transport_config(self, transport_id: str) -> dict[str, Any]:
        """Get configuration for a specific transport.

        Checks [transports.<id>] first, falls back to legacy [telegram] section.
        """
        # Check new format first
        if transport_id in self.transports:
            return self.transports[transport_id]

        # Fall back to legacy telegram section
        if transport_id == "telegram" and self.telegram:
            return {
                "bot_token": self.telegram.bot_token,
                "chat_id": self.telegram.chat_id,
            }

        return {}

    def configured_transport_ids(self) -> list[str]:
        """Get list of transport IDs that have configuration."""
        ids: list[str] = []

        # Check new format
        ids.extend(self.transports.keys())

        # Check legacy telegram section (if not already in transports)
        if "telegram" not in ids and self.telegram:
            ids.append("telegram")

        return ids


def _settings_to_config(settings: WorkspaceSettings, root: Path) -> WorkspaceConfig:
    """Convert WorkspaceSettings to WorkspaceConfig dataclass."""
    # Convert folders
    folders: dict[str, FolderConfig] = {}
    for name, folder_settings in settings.folders.items():
        folders[name] = FolderConfig(
            name=name,
            path=folder_settings.path,
            channels=folder_settings.channels,
            topic_id=folder_settings.topic_id,
            description=folder_settings.description,
            origin=folder_settings.origin,
            pending_topic=folder_settings.pending_topic,
        )

    # Convert ralph
    ralph = RalphConfig(
        enabled=settings.ralph.enabled,
        default_max_iterations=settings.ralph.default_max_iterations,
    )

    # Convert telegram
    telegram: TelegramConfig | None = None
    if settings.telegram:
        telegram = TelegramConfig(
            bot_token=settings.telegram.bot_token.get_secret_value(),
            chat_id=settings.telegram.chat_id,
        )

    # Convert plugins
    plugins = PluginsConfig(
        enabled=settings.plugins.enabled,
        auto_install=settings.plugins.auto_install,
    )

    # Get legacy fields (for backward compatibility)
    # Priority: [telegram] section > [transports.telegram] section > legacy fields
    bot_token = ""
    telegram_group_id = 0
    if settings.telegram:
        bot_token = settings.telegram.bot_token.get_secret_value()
        telegram_group_id = settings.telegram.chat_id
    elif "telegram" in settings.transports:
        # New [transports.telegram] format - extract values for legacy fields
        telegram_transport = settings.transports["telegram"]
        bot_token = telegram_transport.get("bot_token", "")
        telegram_group_id = telegram_transport.get("chat_id", 0)
    elif settings.bot_token:
        bot_token = settings.bot_token.get_secret_value()
        telegram_group_id = settings.telegram_group_id or 0

    return WorkspaceConfig(
        name=settings.name or root.name,
        root=root,
        folders=folders,
        ralph=ralph,
        default_engine=settings.default_engine,
        default_transport=settings.default_transport,
        worktrees_dir=settings.worktrees_dir,
        worktree_base=settings.worktree_base,
        transports=settings.transports,
        telegram=telegram,
        plugins=plugins,
        plugin_configs=settings.plugin_configs,
        telegram_group_id=telegram_group_id,
        bot_token=bot_token,
    )


def load_workspace_config(workspace_root: Path | None = None) -> WorkspaceConfig | None:
    """Load workspace configuration from .pochi/workspace.toml.

    Args:
        workspace_root: Path to workspace root. If None, will search for it.

    Returns:
        WorkspaceConfig if found and valid, None otherwise.
    """
    if workspace_root is None:
        workspace_root = find_workspace_root()
        if workspace_root is None:
            return None

    settings = load_settings(workspace_root)
    if settings is None:
        return None

    return _settings_to_config(settings, workspace_root)


def save_workspace_config(config: WorkspaceConfig) -> None:
    """Save workspace configuration to .pochi/workspace.toml."""
    config_dir = config.root / WORKSPACE_CONFIG_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / WORKSPACE_CONFIG_FILE

    # Build TOML document using tomlkit for proper formatting
    doc = tomlkit.document()

    # [workspace] section
    workspace = tomlkit.table()
    workspace.add("name", config.name)
    if config.default_engine != "claude":
        workspace.add("default_engine", config.default_engine)
    if config.default_transport != "telegram":
        workspace.add("default_transport", config.default_transport)
    if config.worktrees_dir != ".worktrees":
        workspace.add("worktrees_dir", config.worktrees_dir)
    if config.worktree_base:
        workspace.add("worktree_base", config.worktree_base)
    doc.add("workspace", workspace)

    # [transports.<id>] sections (new format)
    if config.transports:
        transports = tomlkit.table()
        for transport_id, transport_cfg in config.transports.items():
            transport_table = tomlkit.table()
            for key, value in transport_cfg.items():
                transport_table.add(key, value)
            transports.add(transport_id, transport_table)
        doc.add("transports", transports)
    elif config.telegram:
        # Fall back to [telegram] section for backwards compatibility
        telegram = tomlkit.table()
        telegram.add("bot_token", config.telegram.bot_token)
        telegram.add("chat_id", config.telegram.chat_id)
        doc.add("telegram", telegram)
    elif config.bot_token:
        # Legacy format fallback
        telegram = tomlkit.table()
        telegram.add("bot_token", config.bot_token)
        telegram.add("chat_id", config.telegram_group_id)
        doc.add("telegram", telegram)

    # [folders.*] sections
    if config.folders:
        folders = tomlkit.table()
        for name, folder in config.folders.items():
            folder_table = tomlkit.table()
            folder_table.add("path", folder.path)
            if folder.channels:
                folder_table.add("channels", folder.channels)
            if folder.topic_id is not None:
                folder_table.add("topic_id", folder.topic_id)
            if folder.description:
                folder_table.add("description", folder.description)
            if folder.origin:
                folder_table.add("origin", folder.origin)
            if folder.pending_topic:
                folder_table.add("pending_topic", True)
            folders.add(name, folder_table)
        doc.add("folders", folders)

    # [workers.ralph] section
    workers = tomlkit.table()
    ralph = tomlkit.table()
    ralph.add("enabled", config.ralph.enabled)
    ralph.add("default_max_iterations", config.ralph.default_max_iterations)
    workers.add("ralph", ralph)
    doc.add("workers", workers)

    config_path.write_text(tomlkit.dumps(doc))
    logger.info("workspace.config.saved", path=str(config_path))


def create_workspace(
    root: Path,
    name: str,
    telegram_group_id: int,
    bot_token: str,
) -> WorkspaceConfig:
    """Create a new workspace configuration."""
    telegram = TelegramConfig(bot_token=bot_token, chat_id=telegram_group_id)
    config = WorkspaceConfig(
        name=name,
        root=root.resolve(),
        telegram=telegram,
        telegram_group_id=telegram_group_id,
        bot_token=bot_token,
    )
    save_workspace_config(config)
    return config


def add_folder_to_workspace(
    config: WorkspaceConfig,
    name: str,
    path: str,
    *,
    description: str | None = None,
    origin: str | None = None,
    pending_topic: bool = True,
) -> FolderConfig:
    """Add a new folder to the workspace configuration."""
    folder = FolderConfig(
        name=name,
        path=path,
        description=description,
        origin=origin,
        pending_topic=pending_topic,
    )
    config.folders[name] = folder
    save_workspace_config(config)
    return folder


def update_folder_topic_id(
    config: WorkspaceConfig,
    folder_name: str,
    topic_id: int,
) -> None:
    """Update a folder's topic_id and clear pending_topic flag."""
    if folder_name not in config.folders:
        return
    config.folders[folder_name].topic_id = topic_id
    config.folders[folder_name].pending_topic = False
    save_workspace_config(config)
