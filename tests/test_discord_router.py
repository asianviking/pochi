"""Tests for Discord workspace router."""

from __future__ import annotations

from pathlib import Path

import pytest

from pochi.chat import ChatUpdate
from pochi.workspace.config import FolderConfig, WorkspaceConfig
from pochi.workspace.discord_router import DiscordWorkspaceRouter, DiscordRouteResult


@pytest.fixture
def workspace_config(tmp_path: Path) -> WorkspaceConfig:
    """Create a workspace config with Discord folders."""
    return WorkspaceConfig(
        name="test-workspace",
        root=tmp_path,
        folders={
            "project-a": FolderConfig(
                name="project-a",
                path="project-a",
                discord_channel_id=111111,
            ),
            "project-b": FolderConfig(
                name="project-b",
                path="project-b",
                discord_channel_id=222222,
            ),
        },
    )


class TestDiscordWorkspaceRouter:
    """Tests for DiscordWorkspaceRouter."""

    def test_routes_to_folder_by_channel(
        self, workspace_config: WorkspaceConfig
    ) -> None:
        """Test that messages in a folder channel are routed correctly."""
        router = DiscordWorkspaceRouter(workspace_config)
        update = ChatUpdate(
            platform="discord",
            update_type="message",
            channel_id=111111,
            message_id=1,
            text="Hello",
            user_id=123,
        )
        result = router.route(update)
        assert not result.is_general
        assert result.folder is not None
        assert result.folder.name == "project-a"
        assert not result.is_thread
        assert result.is_new_conversation

    def test_routes_thread_to_folder(self, workspace_config: WorkspaceConfig) -> None:
        """Test that thread messages are routed to the parent channel's folder."""
        router = DiscordWorkspaceRouter(workspace_config)
        update = ChatUpdate(
            platform="discord",
            update_type="message",
            channel_id=222222,
            thread_id=333333,
            message_id=1,
            text="Thread message",
            user_id=123,
        )
        result = router.route(update)
        assert not result.is_general
        assert result.folder is not None
        assert result.folder.name == "project-b"
        assert result.is_thread
        assert result.thread_id == 333333
        assert not result.is_new_conversation

    def test_routes_unknown_channel_to_general(
        self, workspace_config: WorkspaceConfig
    ) -> None:
        """Test that unknown channels are treated as general."""
        router = DiscordWorkspaceRouter(workspace_config)
        update = ChatUpdate(
            platform="discord",
            update_type="message",
            channel_id=999999,
            message_id=1,
            text="Unknown channel",
            user_id=123,
        )
        result = router.route(update)
        assert result.is_general
        assert result.folder is None

    def test_routes_to_general_channel(
        self, workspace_config: WorkspaceConfig
    ) -> None:
        """Test routing to explicit general channel."""
        router = DiscordWorkspaceRouter(workspace_config, general_channel_id=555555)
        update = ChatUpdate(
            platform="discord",
            update_type="message",
            channel_id=555555,
            message_id=1,
            text="General message",
            user_id=123,
        )
        result = router.route(update)
        assert result.is_general
        assert result.folder is None

    def test_get_folder_by_channel(self, workspace_config: WorkspaceConfig) -> None:
        """Test getting folder by channel ID."""
        router = DiscordWorkspaceRouter(workspace_config)
        folder = router.get_folder_by_channel(111111)
        assert folder is not None
        assert folder.name == "project-a"

        # Unknown channel
        assert router.get_folder_by_channel(999999) is None

    def test_update_folder_channel(self, workspace_config: WorkspaceConfig) -> None:
        """Test updating a folder's channel mapping."""
        router = DiscordWorkspaceRouter(workspace_config)

        # Update project-a's channel
        router.update_folder_channel("project-a", 444444)

        # Old channel should not route to folder
        assert router.get_folder_by_channel(111111) is None

        # New channel should route to folder
        folder = router.get_folder_by_channel(444444)
        assert folder is not None
        assert folder.name == "project-a"

    def test_remove_folder(self, workspace_config: WorkspaceConfig) -> None:
        """Test removing a folder from channel mapping."""
        router = DiscordWorkspaceRouter(workspace_config)

        # Verify folder exists
        assert router.get_folder_by_channel(111111) is not None

        # Remove it
        router.remove_folder("project-a")

        # Should no longer be in mapping
        assert router.get_folder_by_channel(111111) is None
