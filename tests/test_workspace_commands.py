"""Tests for pochi.workspace.commands module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pochi.workspace.config import FolderConfig, WorkspaceConfig, create_workspace
from pochi.workspace.commands import handle_slash_command
from pochi.workspace.router import RouteResult


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
def mock_manager(workspace_config: WorkspaceConfig) -> MagicMock:
    """Create a mock WorkspaceManager."""
    manager = MagicMock()
    manager.config = workspace_config
    manager.send_to_topic = AsyncMock(return_value={"message_id": 1})
    manager.add_folder = AsyncMock(
        return_value=(FolderConfig(name="test", path="test", topic_id=100), 100)
    )
    manager.bot = MagicMock()
    manager.bot.close_forum_topic = AsyncMock(return_value=True)
    return manager


def make_route(
    command: str,
    args: str = "",
    is_general: bool = True,
    folder: FolderConfig | None = None,
) -> RouteResult:
    """Create a RouteResult for testing."""
    return RouteResult(
        is_general=is_general,
        folder=folder,
        is_slash_command=True,
        command=command,
        command_args=args,
        is_unbound_topic=False,
    )


class TestHandleSlashCommand:
    """Tests for handle_slash_command function."""

    @pytest.mark.anyio
    async def test_unknown_command_does_nothing(self, mock_manager: MagicMock) -> None:
        """Test that unknown commands don't send any response."""
        route = make_route("unknown_command")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)
        mock_manager.send_to_topic.assert_not_called()

    @pytest.mark.anyio
    async def test_help_command(self, mock_manager: MagicMock) -> None:
        """Test /help command shows help text."""
        route = make_route("help")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)
        mock_manager.send_to_topic.assert_called_once()
        call_args = mock_manager.send_to_topic.call_args
        text = call_args[0][1]  # Second positional arg is text
        assert "Pochi Workspace Commands" in text
        assert "/clone" in text
        assert "/create" in text
        assert "/list" in text

    @pytest.mark.anyio
    async def test_list_command_empty(self, mock_manager: MagicMock) -> None:
        """Test /list command when no folders exist."""
        route = make_route("list")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)
        mock_manager.send_to_topic.assert_called_once()
        call_args = mock_manager.send_to_topic.call_args
        text = call_args[0][1]
        assert "No folders" in text

    @pytest.mark.anyio
    async def test_list_command_with_folders(
        self, mock_manager: MagicMock, workspace_config: WorkspaceConfig, tmp_path: Path
    ) -> None:
        """Test /list command with folders."""
        # Add a folder
        folder = FolderConfig(
            name="backend",
            path="backend",
            topic_id=100,
            origin="git@github.com:user/backend.git",
        )
        workspace_config.folders["backend"] = folder
        # Create the actual folder
        (tmp_path / "backend").mkdir()

        route = make_route("list")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)
        mock_manager.send_to_topic.assert_called_once()
        call_args = mock_manager.send_to_topic.call_args
        text = call_args[0][1]
        assert "backend" in text
        assert "#backend" in text

    @pytest.mark.anyio
    async def test_status_command(self, mock_manager: MagicMock) -> None:
        """Test /status command."""
        route = make_route("status")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)
        mock_manager.send_to_topic.assert_called_once()
        call_args = mock_manager.send_to_topic.call_args
        text = call_args[0][1]
        assert "Workspace Status" in text
        assert "test-workspace" in text
        assert "Folders:" in text
        assert "Ralph Wiggum" in text

    @pytest.mark.anyio
    async def test_clone_command_no_args(self, mock_manager: MagicMock) -> None:
        """Test /clone command without arguments shows usage."""
        route = make_route("clone", "")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)
        mock_manager.send_to_topic.assert_called_once()
        call_args = mock_manager.send_to_topic.call_args
        text = call_args[0][1]
        assert "Usage: /clone" in text

    @pytest.mark.anyio
    async def test_clone_command_folder_exists(
        self, mock_manager: MagicMock, workspace_config: WorkspaceConfig
    ) -> None:
        """Test /clone command when folder name already exists."""
        workspace_config.folders["existing"] = FolderConfig(
            name="existing", path="existing"
        )
        route = make_route("clone", "existing git@github.com:user/repo.git")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)
        mock_manager.send_to_topic.assert_called_once()
        call_args = mock_manager.send_to_topic.call_args
        text = call_args[0][1]
        assert "already exists" in text

    @pytest.mark.anyio
    async def test_create_command_no_args(self, mock_manager: MagicMock) -> None:
        """Test /create command without arguments shows usage."""
        route = make_route("create", "")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)
        mock_manager.send_to_topic.assert_called_once()
        call_args = mock_manager.send_to_topic.call_args
        text = call_args[0][1]
        assert "Usage: /create" in text

    @pytest.mark.anyio
    async def test_create_command_folder_exists(
        self, mock_manager: MagicMock, workspace_config: WorkspaceConfig
    ) -> None:
        """Test /create command when folder name already exists."""
        workspace_config.folders["existing"] = FolderConfig(
            name="existing", path="existing"
        )
        route = make_route("create", "existing")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)
        mock_manager.send_to_topic.assert_called_once()
        call_args = mock_manager.send_to_topic.call_args
        text = call_args[0][1]
        assert "already exists" in text

    @pytest.mark.anyio
    async def test_add_command_no_args(self, mock_manager: MagicMock) -> None:
        """Test /add command without arguments shows usage."""
        route = make_route("add", "")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)
        mock_manager.send_to_topic.assert_called_once()
        call_args = mock_manager.send_to_topic.call_args
        text = call_args[0][1]
        assert "Usage: /add" in text

    @pytest.mark.anyio
    async def test_add_command_folder_exists(
        self, mock_manager: MagicMock, workspace_config: WorkspaceConfig
    ) -> None:
        """Test /add command when folder name already exists."""
        workspace_config.folders["existing"] = FolderConfig(
            name="existing", path="existing"
        )
        route = make_route("add", "existing /some/path")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)
        mock_manager.send_to_topic.assert_called_once()
        call_args = mock_manager.send_to_topic.call_args
        text = call_args[0][1]
        assert "already exists" in text

    @pytest.mark.anyio
    async def test_add_command_path_not_exists(self, mock_manager: MagicMock) -> None:
        """Test /add command when path doesn't exist."""
        route = make_route("add", "newfolder /nonexistent/path")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)
        mock_manager.send_to_topic.assert_called_once()
        call_args = mock_manager.send_to_topic.call_args
        text = call_args[0][1]
        assert "does not exist" in text

    @pytest.mark.anyio
    async def test_add_command_success(
        self, mock_manager: MagicMock, tmp_path: Path
    ) -> None:
        """Test /add command succeeds when path exists."""
        # Create the directory
        add_path = tmp_path / "newfolder"
        add_path.mkdir()

        route = make_route("add", f"newfolder {add_path}")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)

        # Should call add_folder
        mock_manager.add_folder.assert_called_once()

    @pytest.mark.anyio
    async def test_remove_command_no_args(self, mock_manager: MagicMock) -> None:
        """Test /remove command without arguments shows usage."""
        route = make_route("remove", "")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)
        mock_manager.send_to_topic.assert_called_once()
        call_args = mock_manager.send_to_topic.call_args
        text = call_args[0][1]
        assert "Usage: /remove" in text

    @pytest.mark.anyio
    async def test_remove_command_folder_not_found(
        self, mock_manager: MagicMock
    ) -> None:
        """Test /remove command when folder not found."""
        route = make_route("remove", "nonexistent")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)
        mock_manager.send_to_topic.assert_called_once()
        call_args = mock_manager.send_to_topic.call_args
        text = call_args[0][1]
        assert "not found" in text

    @pytest.mark.anyio
    async def test_remove_command_success(
        self, mock_manager: MagicMock, workspace_config: WorkspaceConfig
    ) -> None:
        """Test /remove command succeeds."""
        workspace_config.folders["todelete"] = FolderConfig(
            name="todelete", path="todelete", topic_id=200
        )
        route = make_route("remove", "todelete")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)

        # Folder should be removed
        assert "todelete" not in workspace_config.folders

    @pytest.mark.anyio
    async def test_engine_command_show_status(self, mock_manager: MagicMock) -> None:
        """Test /engine command without args shows status."""
        route = make_route("engine", "")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)
        mock_manager.send_to_topic.assert_called_once()
        call_args = mock_manager.send_to_topic.call_args
        text = call_args[0][1]
        assert "Engine Configuration" in text
        assert "Default:" in text

    @pytest.mark.anyio
    async def test_engine_command_unknown_engine(self, mock_manager: MagicMock) -> None:
        """Test /engine command with unknown engine."""
        route = make_route("engine", "unknown_engine_xyz")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)
        mock_manager.send_to_topic.assert_called_once()
        call_args = mock_manager.send_to_topic.call_args
        text = call_args[0][1]
        assert "Unknown engine" in text

    @pytest.mark.anyio
    async def test_engine_command_same_engine(
        self, mock_manager: MagicMock, workspace_config: WorkspaceConfig
    ) -> None:
        """Test /engine command when engine is already default."""
        workspace_config.default_engine = "claude"
        route = make_route("engine", "claude")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)
        mock_manager.send_to_topic.assert_called_once()
        call_args = mock_manager.send_to_topic.call_args
        text = call_args[0][1]
        assert "already the default" in text

    @pytest.mark.anyio
    async def test_command_error_handling(self, mock_manager: MagicMock) -> None:
        """Test that command errors are handled gracefully."""
        # Make add_folder raise an exception
        mock_manager.add_folder.side_effect = Exception("Test error")

        route = make_route("create", "newfolder")
        await handle_slash_command(mock_manager, route, reply_to_message_id=1)

        # Should send error message
        calls = mock_manager.send_to_topic.call_args_list
        # Last call should be error message
        last_call_text = calls[-1][0][1]
        assert "Error" in last_call_text
