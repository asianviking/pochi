"""Tests for pochi.workspace.manager module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pochi.workspace.config import (
    FolderConfig,
    WorkspaceConfig,
    create_workspace,
)
from pochi.workspace.manager import WorkspaceManager


@pytest.fixture
def workspace_config(tmp_path: Path) -> WorkspaceConfig:
    """Create a workspace config for testing."""
    return create_workspace(
        root=tmp_path,
        name="test-workspace",
        telegram_group_id=123456,
        bot_token="test-token",
    )


@pytest.fixture
def mock_bot() -> MagicMock:
    """Create a mock BotClient."""
    bot = MagicMock()
    bot.get_chat = AsyncMock(return_value={"is_forum": True, "type": "supergroup"})
    bot.create_forum_topic = AsyncMock(
        return_value={"message_thread_id": 100, "name": "test"}
    )
    bot.close_forum_topic = AsyncMock(return_value=True)
    bot.send_message = AsyncMock(return_value={"message_id": 1})
    return bot


class TestWorkspaceManager:
    """Tests for WorkspaceManager class."""

    def test_creates_manager(
        self, workspace_config: WorkspaceConfig, mock_bot: MagicMock
    ) -> None:
        """Test creating a WorkspaceManager."""
        manager = WorkspaceManager(workspace_config, mock_bot)
        assert manager.config == workspace_config
        assert manager.bot == mock_bot

    def test_set_router(
        self, workspace_config: WorkspaceConfig, mock_bot: MagicMock
    ) -> None:
        """Test setting the router."""
        manager = WorkspaceManager(workspace_config, mock_bot)
        mock_router = MagicMock()
        manager.set_router(mock_router)
        assert manager._workspace_router == mock_router

    def test_reload_router(
        self, workspace_config: WorkspaceConfig, mock_bot: MagicMock
    ) -> None:
        """Test _reload_router calls router.reload_config."""
        manager = WorkspaceManager(workspace_config, mock_bot)
        mock_router = MagicMock()
        manager.set_router(mock_router)
        manager._reload_router()
        mock_router.reload_config.assert_called_once_with(workspace_config)

    def test_reload_router_without_router(
        self, workspace_config: WorkspaceConfig, mock_bot: MagicMock
    ) -> None:
        """Test _reload_router does nothing without router."""
        manager = WorkspaceManager(workspace_config, mock_bot)
        # Should not raise
        manager._reload_router()

    @pytest.mark.anyio
    async def test_check_is_forum_true(
        self, workspace_config: WorkspaceConfig, mock_bot: MagicMock
    ) -> None:
        """Test check_is_forum returns True for forum groups."""
        manager = WorkspaceManager(workspace_config, mock_bot)
        result = await manager.check_is_forum()
        assert result is True
        mock_bot.get_chat.assert_called_once_with(workspace_config.telegram_group_id)

    @pytest.mark.anyio
    async def test_check_is_forum_false(
        self, workspace_config: WorkspaceConfig, mock_bot: MagicMock
    ) -> None:
        """Test check_is_forum returns False for non-forum groups."""
        mock_bot.get_chat.return_value = {"is_forum": False, "type": "group"}
        manager = WorkspaceManager(workspace_config, mock_bot)
        result = await manager.check_is_forum()
        assert result is False

    @pytest.mark.anyio
    async def test_check_is_forum_error(
        self, workspace_config: WorkspaceConfig, mock_bot: MagicMock
    ) -> None:
        """Test check_is_forum returns False on error."""
        mock_bot.get_chat.return_value = None
        manager = WorkspaceManager(workspace_config, mock_bot)
        result = await manager.check_is_forum()
        assert result is False

    @pytest.mark.anyio
    async def test_create_topic_for_folder(
        self, workspace_config: WorkspaceConfig, mock_bot: MagicMock
    ) -> None:
        """Test create_topic_for_folder creates a topic."""
        manager = WorkspaceManager(workspace_config, mock_bot)
        folder = FolderConfig(name="test-folder", path="test-folder")

        topic_id = await manager.create_topic_for_folder(folder)

        assert topic_id == 100
        mock_bot.create_forum_topic.assert_called_once_with(
            chat_id=workspace_config.telegram_group_id,
            name="test-folder",
        )

    @pytest.mark.anyio
    async def test_create_topic_for_folder_failure(
        self, workspace_config: WorkspaceConfig, mock_bot: MagicMock
    ) -> None:
        """Test create_topic_for_folder returns None on failure."""
        mock_bot.create_forum_topic.return_value = None
        manager = WorkspaceManager(workspace_config, mock_bot)
        folder = FolderConfig(name="test-folder", path="test-folder")

        topic_id = await manager.create_topic_for_folder(folder)

        assert topic_id is None

    @pytest.mark.anyio
    async def test_create_topic_for_folder_no_thread_id(
        self, workspace_config: WorkspaceConfig, mock_bot: MagicMock
    ) -> None:
        """Test create_topic_for_folder returns None when no thread_id."""
        mock_bot.create_forum_topic.return_value = {"name": "test"}
        manager = WorkspaceManager(workspace_config, mock_bot)
        folder = FolderConfig(name="test-folder", path="test-folder")

        topic_id = await manager.create_topic_for_folder(folder)

        assert topic_id is None

    @pytest.mark.anyio
    async def test_process_pending_topics(
        self, workspace_config: WorkspaceConfig, mock_bot: MagicMock, tmp_path: Path
    ) -> None:
        """Test process_pending_topics creates topics for pending folders."""
        # Add a pending folder
        workspace_config.folders["pending"] = FolderConfig(
            name="pending", path="pending", pending_topic=True
        )
        # Save the config so reload works
        from pochi.workspace.config import save_workspace_config

        save_workspace_config(workspace_config)

        manager = WorkspaceManager(workspace_config, mock_bot)
        created = await manager.process_pending_topics()

        assert len(created) == 1
        assert created[0] == ("pending", 100)

    @pytest.mark.anyio
    async def test_process_pending_topics_empty(
        self, workspace_config: WorkspaceConfig, mock_bot: MagicMock
    ) -> None:
        """Test process_pending_topics with no pending topics."""
        manager = WorkspaceManager(workspace_config, mock_bot)
        created = await manager.process_pending_topics()
        assert created == []

    @pytest.mark.anyio
    async def test_add_folder(
        self, workspace_config: WorkspaceConfig, mock_bot: MagicMock
    ) -> None:
        """Test add_folder adds a folder and creates topic."""
        manager = WorkspaceManager(workspace_config, mock_bot)

        folder, topic_id = await manager.add_folder(
            name="new-folder",
            path="new-folder",
            description="A new folder",
            origin="git@github.com:user/repo.git",
            create_topic=True,
        )

        assert folder.name == "new-folder"
        assert folder.description == "A new folder"
        assert folder.origin == "git@github.com:user/repo.git"
        assert topic_id == 100

    @pytest.mark.anyio
    async def test_add_folder_no_topic(
        self, workspace_config: WorkspaceConfig, mock_bot: MagicMock
    ) -> None:
        """Test add_folder without creating topic."""
        manager = WorkspaceManager(workspace_config, mock_bot)

        folder, topic_id = await manager.add_folder(
            name="new-folder",
            path="new-folder",
            create_topic=False,
        )

        assert folder.name == "new-folder"
        assert topic_id is None
        mock_bot.create_forum_topic.assert_not_called()

    @pytest.mark.anyio
    async def test_send_to_topic(
        self, workspace_config: WorkspaceConfig, mock_bot: MagicMock
    ) -> None:
        """Test send_to_topic sends a message."""
        manager = WorkspaceManager(workspace_config, mock_bot)

        result = await manager.send_to_topic(
            topic_id=100,
            text="Hello world",
            reply_to_message_id=1,
        )

        assert result == {"message_id": 1}
        mock_bot.send_message.assert_called_once_with(
            chat_id=workspace_config.telegram_group_id,
            text="Hello world",
            message_thread_id=100,
            reply_to_message_id=1,
            disable_notification=False,
            parse_mode=None,
        )

    @pytest.mark.anyio
    async def test_send_to_general_topic(
        self, workspace_config: WorkspaceConfig, mock_bot: MagicMock
    ) -> None:
        """Test send_to_topic with None topic_id sends to General."""
        manager = WorkspaceManager(workspace_config, mock_bot)

        await manager.send_to_topic(None, "Hello General")

        mock_bot.send_message.assert_called_once()
        call_kwargs = mock_bot.send_message.call_args[1]
        assert call_kwargs["message_thread_id"] is None

    @pytest.mark.anyio
    async def test_send_unbound_topic_error(
        self, workspace_config: WorkspaceConfig, mock_bot: MagicMock
    ) -> None:
        """Test send_unbound_topic_error sends error message."""
        manager = WorkspaceManager(workspace_config, mock_bot)

        await manager.send_unbound_topic_error(topic_id=100, reply_to_message_id=1)

        mock_bot.send_message.assert_called_once()
        call_args = mock_bot.send_message.call_args
        text = call_args[1]["text"]
        assert "not bound to a folder" in text
