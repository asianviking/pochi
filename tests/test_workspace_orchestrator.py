"""Tests for pochi.workspace.orchestrator module."""

from __future__ import annotations

from pathlib import Path


from pochi.workspace.config import FolderConfig, WorkspaceConfig
from pochi.workspace.orchestrator import (
    build_orchestrator_context,
    prepend_orchestrator_context,
)


class TestBuildOrchestratorContext:
    """Tests for build_orchestrator_context function."""

    def test_basic_context_structure(self, tmp_path: Path) -> None:
        """Test basic context structure is generated correctly."""
        config = WorkspaceConfig(
            name="test-workspace",
            root=tmp_path,
            telegram_group_id=123,
            bot_token="token",
        )
        context = build_orchestrator_context(config)

        assert "# Pochi Workspace Context" in context
        assert "test-workspace" in context
        assert str(tmp_path) in context
        assert "No folders in this workspace yet." in context

    def test_context_with_folders(self, tmp_path: Path) -> None:
        """Test context includes folder information."""
        # Create a folder that looks like a git repo
        repo_dir = tmp_path / "backend"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()

        folders = {
            "backend": FolderConfig(
                name="backend",
                path="backend",
                topic_id=100,
                description="API server",
                origin="git@github.com:user/backend.git",
            ),
            "frontend": FolderConfig(
                name="frontend",
                path="frontend",
                topic_id=200,
            ),
        }
        config = WorkspaceConfig(
            name="test-workspace",
            root=tmp_path,
            telegram_group_id=123,
            bot_token="token",
            folders=folders,
        )
        context = build_orchestrator_context(config)

        assert "## Folders" in context
        assert "**backend**" in context
        assert "(git)" in context
        assert "topic #100" in context
        assert "API server" in context
        assert "git@github.com:user/backend.git" in context
        assert "**frontend**" in context
        assert "topic #200" in context

    def test_context_with_folder_no_topic(self, tmp_path: Path) -> None:
        """Test context shows 'no topic' for folders without topic_id."""
        folders = {
            "pending": FolderConfig(
                name="pending",
                path="pending",
                pending_topic=True,
            ),
        }
        config = WorkspaceConfig(
            name="test-workspace",
            root=tmp_path,
            telegram_group_id=123,
            bot_token="token",
            folders=folders,
        )
        context = build_orchestrator_context(config)

        assert "no topic" in context

    def test_context_includes_capabilities(self, tmp_path: Path) -> None:
        """Test context includes capabilities section."""
        config = WorkspaceConfig(
            name="test-workspace",
            root=tmp_path,
            telegram_group_id=123,
            bot_token="token",
        )
        context = build_orchestrator_context(config)

        assert "## Your Capabilities" in context
        assert "orchestrator" in context
        assert "git clone" in context
        assert "gh" in context

    def test_context_includes_slash_commands(self, tmp_path: Path) -> None:
        """Test context includes slash commands documentation."""
        config = WorkspaceConfig(
            name="test-workspace",
            root=tmp_path,
            telegram_group_id=123,
            bot_token="token",
        )
        context = build_orchestrator_context(config)

        assert "## Available Slash Commands" in context
        assert "/clone" in context
        assert "/create" in context
        assert "/add" in context
        assert "/list" in context
        assert "/remove" in context
        assert "/status" in context
        assert "/help" in context


class TestPrependOrchestratorContext:
    """Tests for prepend_orchestrator_context function."""

    def test_prepends_context_to_message(self, tmp_path: Path) -> None:
        """Test that context is prepended to user message."""
        config = WorkspaceConfig(
            name="test-workspace",
            root=tmp_path,
            telegram_group_id=123,
            bot_token="token",
        )
        user_message = "Hello, I need help!"
        result = prepend_orchestrator_context(config, user_message)

        assert result.startswith("# Pochi Workspace Context")
        assert "---" in result
        assert result.endswith("Hello, I need help!")

    def test_separator_between_context_and_message(self, tmp_path: Path) -> None:
        """Test that separator exists between context and message."""
        config = WorkspaceConfig(
            name="test-workspace",
            root=tmp_path,
            telegram_group_id=123,
            bot_token="token",
        )
        result = prepend_orchestrator_context(config, "Test message")

        # Context should be separated by "---"
        parts = result.split("---")
        assert len(parts) == 2
        assert "Pochi Workspace Context" in parts[0]
        assert "Test message" in parts[1]

    def test_preserves_multiline_message(self, tmp_path: Path) -> None:
        """Test that multiline user messages are preserved."""
        config = WorkspaceConfig(
            name="test-workspace",
            root=tmp_path,
            telegram_group_id=123,
            bot_token="token",
        )
        user_message = "Line 1\nLine 2\nLine 3"
        result = prepend_orchestrator_context(config, user_message)

        assert "Line 1\nLine 2\nLine 3" in result
