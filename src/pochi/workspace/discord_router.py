"""Discord message routing for workspace channels.

Routes messages to the appropriate folder based on Discord channel ID.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..chat import ChatUpdate
from .config import FolderConfig, WorkspaceConfig


@dataclass
class DiscordRouteResult:
    """Result of routing a Discord message."""

    is_general: bool  # True if this is the #general channel
    folder: FolderConfig | None  # The folder for this channel (if any)
    is_thread: bool  # True if message is in a thread
    thread_id: int | None  # The thread ID (if in a thread)
    is_new_conversation: bool  # True if this starts a new conversation


class DiscordWorkspaceRouter:
    """Routes Discord messages to the appropriate workspace folder."""

    def __init__(
        self,
        config: WorkspaceConfig,
        *,
        general_channel_id: int | None = None,
    ) -> None:
        """Initialize the Discord router.

        Args:
            config: The workspace configuration
            general_channel_id: The ID of the #general channel (optional)
        """
        self._config = config
        self._general_channel_id = general_channel_id
        self._channel_to_folder: dict[int, FolderConfig] = {}

        # Build channel -> folder mapping
        for folder in config.folders.values():
            if folder.discord_channel_id is not None:
                self._channel_to_folder[folder.discord_channel_id] = folder

    def route(self, update: ChatUpdate) -> DiscordRouteResult:
        """Route a chat update to the appropriate folder.

        Args:
            update: The chat update to route

        Returns:
            DiscordRouteResult with routing information
        """
        channel_id = update.channel_id
        thread_id = update.thread_id
        is_thread = thread_id is not None

        # Check if this is the general channel
        if channel_id == self._general_channel_id:
            return DiscordRouteResult(
                is_general=True,
                folder=None,
                is_thread=is_thread,
                thread_id=thread_id,
                is_new_conversation=not is_thread,
            )

        # Check if this channel maps to a folder
        folder = self._channel_to_folder.get(channel_id)
        if folder is not None:
            return DiscordRouteResult(
                is_general=False,
                folder=folder,
                is_thread=is_thread,
                thread_id=thread_id,
                is_new_conversation=not is_thread,
            )

        # Unknown channel - treat as general for orchestrator
        return DiscordRouteResult(
            is_general=True,
            folder=None,
            is_thread=is_thread,
            thread_id=thread_id,
            is_new_conversation=not is_thread,
        )

    def get_folder_by_channel(self, channel_id: int) -> FolderConfig | None:
        """Get folder config for a channel ID."""
        return self._channel_to_folder.get(channel_id)

    def update_folder_channel(self, folder_name: str, channel_id: int) -> None:
        """Update the channel mapping for a folder."""
        folder = self._config.folders.get(folder_name)
        if folder is not None:
            # Remove old mapping if exists
            if folder.discord_channel_id is not None:
                self._channel_to_folder.pop(folder.discord_channel_id, None)
            # Set new mapping
            folder.discord_channel_id = channel_id
            self._channel_to_folder[channel_id] = folder

    def remove_folder(self, folder_name: str) -> None:
        """Remove a folder from the channel mapping."""
        folder = self._config.folders.get(folder_name)
        if folder is not None and folder.discord_channel_id is not None:
            self._channel_to_folder.pop(folder.discord_channel_id, None)
