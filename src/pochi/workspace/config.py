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
class TelegramConfig:
    """Telegram platform configuration."""

    bot_token: str
    group_id: int
    admin_user: int | None = None
    allowed_users: list[int] = field(default_factory=list)

    def is_admin(self, user_id: int) -> bool:
        """Check if a user is the admin."""
        return self.admin_user is not None and self.admin_user == user_id

    def is_authorized(self, user_id: int) -> bool:
        """Check if a user is authorized (admin or guest)."""
        if self.admin_user is None:
            return True
        return self.admin_user == user_id or user_id in self.allowed_users


@dataclass
class DiscordConfig:
    """Discord platform configuration."""

    bot_token: str
    guild_id: int
    category_id: int
    admin_user: int | None = None
    allowed_users: list[int] = field(default_factory=list)

    def is_admin(self, user_id: int) -> bool:
        """Check if a user is the admin."""
        return self.admin_user is not None and self.admin_user == user_id

    def is_authorized(self, user_id: int) -> bool:
        """Check if a user is authorized (admin or guest)."""
        if self.admin_user is None:
            return True
        return self.admin_user == user_id or user_id in self.allowed_users


@dataclass
class FolderConfig:
    """Configuration for a folder in the workspace (repo or plain directory)."""

    name: str
    path: str  # Relative to workspace root
    topic_id: int | None = None  # Telegram topic ID (legacy, also used for migration)
    telegram_topic_id: int | None = None  # Telegram forum topic ID
    discord_channel_id: int | None = None  # Discord channel ID
    description: str | None = None
    origin: str | None = None  # Git remote URL if cloned
    pending_topic: bool = False  # True if Telegram topic needs to be created
    pending_channel: bool = False  # True if Discord channel needs to be created

    def absolute_path(self, workspace_root: Path) -> Path:
        """Get the absolute path to this folder."""
        return workspace_root / self.path

    def is_git_repo(self, workspace_root: Path) -> bool:
        """Check if this folder is a git repository."""
        git_dir = self.absolute_path(workspace_root) / ".git"
        return git_dir.exists()

    def get_telegram_topic_id(self) -> int | None:
        """Get the Telegram topic ID (handles legacy migration)."""
        return self.telegram_topic_id or self.topic_id


@dataclass
class WorkspaceConfig:
    """Configuration for a workspace with multiple folders."""

    name: str
    root: Path  # Absolute path to workspace root
    folders: dict[str, FolderConfig] = field(default_factory=dict)
    ralph: RalphConfig = field(default_factory=RalphConfig)
    default_engine: str = "claude"

    # Platform-specific configs (at least one should be set)
    telegram: TelegramConfig | None = None
    discord: DiscordConfig | None = None

    # Legacy fields for backwards compatibility (migrated to telegram config)
    telegram_group_id: int = 0
    bot_token: str = ""
    admin_user: int | None = None
    allowed_users: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Migrate legacy config to new structure if needed."""
        # If we have legacy config but no telegram config, create one
        if self.telegram is None and self.telegram_group_id != 0 and self.bot_token:
            self.telegram = TelegramConfig(
                bot_token=self.bot_token,
                group_id=self.telegram_group_id,
                admin_user=self.admin_user,
                allowed_users=list(self.allowed_users),
            )

    def is_admin(self, user_id: int) -> bool:
        """Check if a user is the admin (for any platform)."""
        if self.telegram is not None and self.telegram.is_admin(user_id):
            return True
        if self.discord is not None and self.discord.is_admin(user_id):
            return True
        # Legacy check
        return self.admin_user is not None and self.admin_user == user_id

    def is_authorized(self, user_id: int) -> bool:
        """Check if a user is authorized (admin or guest, for any platform)."""
        # Check platform-specific configs
        if self.telegram is not None and self.telegram.is_authorized(user_id):
            return True
        if self.discord is not None and self.discord.is_authorized(user_id):
            return True
        # Legacy check
        if self.admin_user is None:
            return True
        return self.admin_user == user_id or user_id in self.allowed_users

    def add_guest(self, user_id: int, platform: str = "telegram") -> bool:
        """Add a guest user for a specific platform."""
        if platform == "telegram" and self.telegram is not None:
            if self.telegram.is_admin(user_id):
                return False
            if user_id in self.telegram.allowed_users:
                return False
            self.telegram.allowed_users.append(user_id)
            return True
        elif platform == "discord" and self.discord is not None:
            if self.discord.is_admin(user_id):
                return False
            if user_id in self.discord.allowed_users:
                return False
            self.discord.allowed_users.append(user_id)
            return True
        # Legacy fallback
        if user_id == self.admin_user:
            return False
        if user_id in self.allowed_users:
            return False
        self.allowed_users.append(user_id)
        return True

    def remove_guest(self, user_id: int, platform: str = "telegram") -> bool:
        """Remove a guest user from a specific platform."""
        if platform == "telegram" and self.telegram is not None:
            if user_id not in self.telegram.allowed_users:
                return False
            self.telegram.allowed_users.remove(user_id)
            return True
        elif platform == "discord" and self.discord is not None:
            if user_id not in self.discord.allowed_users:
                return False
            self.discord.allowed_users.remove(user_id)
            return True
        # Legacy fallback
        if user_id not in self.allowed_users:
            return False
        self.allowed_users.remove(user_id)
        return True

    def get_folder_by_topic(self, topic_id: int) -> FolderConfig | None:
        """Find a folder by its Telegram topic ID."""
        for folder in self.folders.values():
            if folder.get_telegram_topic_id() == topic_id:
                return folder
        return None

    def get_folder_by_discord_channel(self, channel_id: int) -> FolderConfig | None:
        """Find a folder by its Discord channel ID."""
        for folder in self.folders.values():
            if folder.discord_channel_id == channel_id:
                return folder
        return None

    def get_pending_topics(self) -> list[FolderConfig]:
        """Get all folders that need Telegram topics created."""
        return [folder for folder in self.folders.values() if folder.pending_topic]

    def get_pending_channels(self) -> list[FolderConfig]:
        """Get all folders that need Discord channels created."""
        return [folder for folder in self.folders.values() if folder.pending_channel]

    def config_path(self) -> Path:
        """Get the path to the workspace config file."""
        return self.root / WORKSPACE_CONFIG_DIR / WORKSPACE_CONFIG_FILE

    @property
    def has_telegram(self) -> bool:
        """Check if Telegram is configured."""
        return self.telegram is not None or (
            self.telegram_group_id != 0 and self.bot_token != ""
        )

    @property
    def has_discord(self) -> bool:
        """Check if Discord is configured."""
        return self.discord is not None

    def get_telegram_group_id(self) -> int:
        """Get the Telegram group ID (handles legacy config)."""
        if self.telegram is not None:
            return self.telegram.group_id
        return self.telegram_group_id

    def get_telegram_bot_token(self) -> str:
        """Get the Telegram bot token (handles legacy config)."""
        if self.telegram is not None:
            return self.telegram.bot_token
        return self.bot_token


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
            telegram_topic_id=folder_data.get("telegram_topic_id"),
            discord_channel_id=folder_data.get("discord_channel_id"),
            description=folder_data.get("description"),
            origin=folder_data.get("origin"),
            pending_topic=folder_data.get("pending_topic", False),
            pending_channel=folder_data.get("pending_channel", False),
        )

    # Parse ralph config
    ralph_data = data.get("workers", {}).get("ralph", {})
    ralph = RalphConfig(
        enabled=ralph_data.get("enabled", False),
        default_max_iterations=ralph_data.get("default_max_iterations", 3),
    )

    # Parse telegram config (new format)
    telegram: TelegramConfig | None = None
    telegram_data = data.get("telegram", {})
    if telegram_data:
        telegram = TelegramConfig(
            bot_token=telegram_data.get("bot_token", ""),
            group_id=telegram_data.get("group_id", 0),
            admin_user=telegram_data.get("admin_user"),
            allowed_users=telegram_data.get("allowed_users", []),
        )

    # Parse discord config
    discord: DiscordConfig | None = None
    discord_data = data.get("discord", {})
    if discord_data:
        discord = DiscordConfig(
            bot_token=discord_data.get("bot_token", ""),
            guild_id=discord_data.get("guild_id", 0),
            category_id=discord_data.get("category_id", 0),
            admin_user=discord_data.get("admin_user"),
            allowed_users=discord_data.get("allowed_users", []),
        )

    return WorkspaceConfig(
        name=workspace_data.get("name", root.name),
        root=root,
        folders=folders,
        ralph=ralph,
        default_engine=workspace_data.get("default_engine", "claude"),
        telegram=telegram,
        discord=discord,
        # Legacy fields for backwards compatibility
        telegram_group_id=workspace_data.get("telegram_group_id", 0),
        bot_token=workspace_data.get("bot_token", ""),
        admin_user=workspace_data.get("admin_user"),
        allowed_users=workspace_data.get("allowed_users", []),
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
    if config.default_engine != "claude":
        lines.append(f'default_engine = "{config.default_engine}"')

    # Legacy fields (only if new config format not used)
    if config.telegram is None:
        if config.telegram_group_id != 0:
            lines.append(f"telegram_group_id = {config.telegram_group_id}")
        if config.bot_token:
            lines.append(f'bot_token = "{config.bot_token}"')
        if config.admin_user is not None:
            lines.append(f"admin_user = {config.admin_user}")
        if config.allowed_users:
            lines.append(f"allowed_users = {config.allowed_users}")
    lines.append("")

    # Telegram section (new format)
    if config.telegram is not None:
        lines.append("[telegram]")
        lines.append(f'bot_token = "{config.telegram.bot_token}"')
        lines.append(f"group_id = {config.telegram.group_id}")
        if config.telegram.admin_user is not None:
            lines.append(f"admin_user = {config.telegram.admin_user}")
        if config.telegram.allowed_users:
            lines.append(f"allowed_users = {config.telegram.allowed_users}")
        lines.append("")

    # Discord section
    if config.discord is not None:
        lines.append("[discord]")
        lines.append(f'bot_token = "{config.discord.bot_token}"')
        lines.append(f"guild_id = {config.discord.guild_id}")
        lines.append(f"category_id = {config.discord.category_id}")
        if config.discord.admin_user is not None:
            lines.append(f"admin_user = {config.discord.admin_user}")
        if config.discord.allowed_users:
            lines.append(f"allowed_users = {config.discord.allowed_users}")
        lines.append("")

    # Folders sections
    for name, folder in config.folders.items():
        lines.append(f"[folders.{name}]")
        lines.append(f'path = "{folder.path}"')
        # Write telegram_topic_id if set, otherwise fall back to legacy topic_id
        if folder.telegram_topic_id is not None:
            lines.append(f"telegram_topic_id = {folder.telegram_topic_id}")
        elif folder.topic_id is not None:
            lines.append(f"topic_id = {folder.topic_id}")
        if folder.discord_channel_id is not None:
            lines.append(f"discord_channel_id = {folder.discord_channel_id}")
        if folder.description:
            lines.append(f'description = "{folder.description}"')
        if folder.origin:
            lines.append(f'origin = "{folder.origin}"')
        if folder.pending_topic:
            lines.append("pending_topic = true")
        if folder.pending_channel:
            lines.append("pending_channel = true")
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
    pending_topic: bool = False,
    pending_channel: bool = False,
) -> FolderConfig:
    """Add a new folder to the workspace configuration.

    Args:
        config: The workspace config to add to
        name: Folder name
        path: Relative path to folder
        description: Optional description
        origin: Git remote URL if cloned
        pending_topic: True if Telegram topic needs to be created
        pending_channel: True if Discord channel needs to be created
    """
    # Determine which platforms need channels/topics
    if config.has_telegram:
        pending_topic = True
    if config.has_discord:
        pending_channel = True

    folder = FolderConfig(
        name=name,
        path=path,
        description=description,
        origin=origin,
        pending_topic=pending_topic,
        pending_channel=pending_channel,
    )
    config.folders[name] = folder
    save_workspace_config(config)
    return folder


def update_folder_topic_id(
    config: WorkspaceConfig,
    folder_name: str,
    topic_id: int,
) -> None:
    """Update a folder's Telegram topic_id and clear pending_topic flag."""
    if folder_name not in config.folders:
        return
    config.folders[folder_name].telegram_topic_id = topic_id
    config.folders[folder_name].topic_id = topic_id  # Also set legacy field
    config.folders[folder_name].pending_topic = False
    save_workspace_config(config)


def update_folder_discord_channel_id(
    config: WorkspaceConfig,
    folder_name: str,
    channel_id: int,
) -> None:
    """Update a folder's Discord channel_id and clear pending_channel flag."""
    if folder_name not in config.folders:
        return
    config.folders[folder_name].discord_channel_id = channel_id
    config.folders[folder_name].pending_channel = False
    save_workspace_config(config)
