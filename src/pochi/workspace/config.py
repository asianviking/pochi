"""Workspace configuration loading and management."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomllib

from ..logging import get_logger

logger = get_logger(__name__)

WORKSPACE_CONFIG_DIR = ".pochi"
WORKSPACE_CONFIG_FILE = "workspace.toml"


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
    topic_id: int | None = None
    description: str | None = None
    origin: str | None = None  # Git remote URL if cloned
    pending_topic: bool = False  # True if topic needs to be created

    def absolute_path(self, workspace_root: Path) -> Path:
        """Get the absolute path to this folder."""
        return workspace_root / self.path

    def is_git_repo(self, workspace_root: Path) -> bool:
        """Check if this folder is a git repository."""
        git_dir = self.absolute_path(workspace_root) / ".git"
        return git_dir.exists()


@dataclass
class WorkspaceConfig:
    """Configuration for a workspace with multiple folders."""

    name: str
    root: Path  # Absolute path to workspace root
    telegram_group_id: int
    bot_token: str
    folders: dict[str, FolderConfig] = field(default_factory=dict)
    ralph: RalphConfig = field(default_factory=RalphConfig)
    default_engine: str = "claude"

    def get_folder_by_topic(self, topic_id: int) -> FolderConfig | None:
        """Find a folder by its Telegram topic ID."""
        for folder in self.folders.values():
            if folder.topic_id == topic_id:
                return folder
        return None

    def get_pending_topics(self) -> list[FolderConfig]:
        """Get all folders that need topics created."""
        return [folder for folder in self.folders.values() if folder.pending_topic]

    def config_path(self) -> Path:
        """Get the path to the workspace config file."""
        return self.root / WORKSPACE_CONFIG_DIR / WORKSPACE_CONFIG_FILE


def find_workspace_root(start_path: Path | None = None) -> Path | None:
    """Walk up from start_path to find a workspace root (contains .pochi/workspace.toml)."""
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


def load_workspace_config(workspace_root: Path | None = None) -> WorkspaceConfig | None:
    """Load workspace configuration from .pochi/workspace.toml."""
    if workspace_root is None:
        workspace_root = find_workspace_root()
        if workspace_root is None:
            return None

    config_path = workspace_root / WORKSPACE_CONFIG_DIR / WORKSPACE_CONFIG_FILE
    if not config_path.exists():
        return None

    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        logger.error(
            "workspace.config.load_failed",
            path=str(config_path),
            error=str(e),
        )
        return None

    return _parse_workspace_config(data, workspace_root)


def _parse_workspace_config(data: dict[str, Any], root: Path) -> WorkspaceConfig:
    """Parse raw TOML data into WorkspaceConfig."""
    workspace_data = data.get("workspace", {})

    # Parse folders (with migration from legacy [repos.*] section)
    folders: dict[str, FolderConfig] = {}
    folders_data = data.get("folders", {})

    # Migrate from legacy [repos.*] if [folders.*] doesn't exist
    if not folders_data and "repos" in data:
        folders_data = data.get("repos", {})

    for name, folder_data in folders_data.items():
        folders[name] = FolderConfig(
            name=name,
            path=folder_data.get("path", name),
            topic_id=folder_data.get("topic_id"),
            description=folder_data.get("description"),
            origin=folder_data.get("origin"),
            pending_topic=folder_data.get("pending_topic", False),
        )

    # Parse ralph config
    ralph_data = data.get("workers", {}).get("ralph", {})
    ralph = RalphConfig(
        enabled=ralph_data.get("enabled", False),
        default_max_iterations=ralph_data.get("default_max_iterations", 3),
    )

    return WorkspaceConfig(
        name=workspace_data.get("name", root.name),
        root=root,
        telegram_group_id=workspace_data.get("telegram_group_id", 0),
        bot_token=workspace_data.get("bot_token", ""),
        folders=folders,
        ralph=ralph,
        default_engine=workspace_data.get("default_engine", "claude"),
    )


def save_workspace_config(config: WorkspaceConfig) -> None:
    """Save workspace configuration to .pochi/workspace.toml."""
    config_dir = config.root / WORKSPACE_CONFIG_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / WORKSPACE_CONFIG_FILE

    lines: list[str] = []

    # Workspace section
    lines.append("[workspace]")
    lines.append(f'name = "{config.name}"')
    lines.append(f"telegram_group_id = {config.telegram_group_id}")
    lines.append(f'bot_token = "{config.bot_token}"')
    if config.default_engine != "claude":
        lines.append(f'default_engine = "{config.default_engine}"')
    lines.append("")

    # Folders sections
    for name, folder in config.folders.items():
        lines.append(f"[folders.{name}]")
        lines.append(f'path = "{folder.path}"')
        if folder.topic_id is not None:
            lines.append(f"topic_id = {folder.topic_id}")
        if folder.description:
            lines.append(f'description = "{folder.description}"')
        if folder.origin:
            lines.append(f'origin = "{folder.origin}"')
        if folder.pending_topic:
            lines.append("pending_topic = true")
        lines.append("")

    # Ralph section
    lines.append("[workers.ralph]")
    lines.append(f"enabled = {'true' if config.ralph.enabled else 'false'}")
    lines.append(f"default_max_iterations = {config.ralph.default_max_iterations}")
    lines.append("")

    with open(config_path, "w") as f:
        f.write("\n".join(lines))

    logger.info("workspace.config.saved", path=str(config_path))


def create_workspace(
    root: Path,
    name: str,
    telegram_group_id: int,
    bot_token: str,
) -> WorkspaceConfig:
    """Create a new workspace configuration."""
    config = WorkspaceConfig(
        name=name,
        root=root.resolve(),
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
