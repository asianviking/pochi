"""Tests for pochi.workspace.router module."""

from __future__ import annotations

from pathlib import Path

import pytest

from pochi.workspace.config import (
    FolderConfig,
    RalphConfig,
    WorkspaceConfig,
)
from pochi.workspace.router import (
    GENERAL_SLASH_COMMANDS,
    RouteResult,
    WorkspaceRouter,
    extract_context_from_text,
    is_general_slash_command,
    parse_branch_directive,
    parse_slash_command,
)


class TestParseSlashCommand:
    """Tests for parse_slash_command function."""

    def test_parses_simple_command(self) -> None:
        """Test parsing a simple slash command."""
        cmd, args = parse_slash_command("/help")
        assert cmd == "help"
        assert args == ""

    def test_parses_command_with_args(self) -> None:
        """Test parsing a command with arguments."""
        cmd, args = parse_slash_command("/clone myrepo git@github.com:user/repo.git")
        assert cmd == "clone"
        assert args == "myrepo git@github.com:user/repo.git"

    def test_parses_command_with_bot_suffix(self) -> None:
        """Test parsing a command with @botname suffix."""
        cmd, args = parse_slash_command("/help@my_bot")
        assert cmd == "help"
        assert args == ""

    def test_parses_command_with_bot_suffix_and_args(self) -> None:
        """Test parsing a command with @botname suffix and arguments."""
        cmd, args = parse_slash_command("/clone@pochi_bot myrepo url")
        assert cmd == "clone"
        assert args == "myrepo url"

    def test_parses_multiline_command(self) -> None:
        """Test parsing a command with multiline content."""
        cmd, args = parse_slash_command("/claude\nHello world\nHow are you?")
        assert cmd == "claude"
        assert args == "Hello world\nHow are you?"

    def test_parses_command_with_args_and_newline(self) -> None:
        """Test parsing a command with args on first line and more lines."""
        cmd, args = parse_slash_command("/ralph some prompt\nmore content")
        assert cmd == "ralph"
        assert args == "some prompt\nmore content"

    def test_returns_none_for_non_command(self) -> None:
        """Test non-command text returns None command."""
        cmd, args = parse_slash_command("hello world")
        assert cmd is None
        assert args == "hello world"

    def test_returns_none_for_empty_string(self) -> None:
        """Test empty string returns None command."""
        cmd, args = parse_slash_command("")
        assert cmd is None
        assert args == ""


class TestRouteResult:
    """Tests for RouteResult dataclass."""

    def test_general_route(self) -> None:
        """Test creating a general topic route result."""
        route = RouteResult(
            is_general=True,
            folder=None,
            is_slash_command=True,
            command="help",
            command_args="",
            is_unbound_topic=False,
        )
        assert route.is_general is True
        assert route.folder is None
        assert route.is_slash_command is True
        assert route.command == "help"

    def test_folder_route(self) -> None:
        """Test creating a folder topic route result."""
        folder = FolderConfig(name="backend", path="backend", topic_id=123)
        route = RouteResult(
            is_general=False,
            folder=folder,
            is_slash_command=False,
            command=None,
            command_args="some text",
            is_unbound_topic=False,
        )
        assert route.is_general is False
        assert route.folder is folder
        assert route.is_slash_command is False


class TestWorkspaceRouter:
    """Tests for WorkspaceRouter class."""

    @pytest.fixture
    def workspace_config(self, tmp_path: Path) -> WorkspaceConfig:
        """Create a workspace config for testing."""
        folders = {
            "frontend": FolderConfig(name="frontend", path="frontend", topic_id=100),
            "backend": FolderConfig(name="backend", path="backend", topic_id=200),
            "pending": FolderConfig(name="pending", path="pending", pending_topic=True),
        }
        return WorkspaceConfig(
            name="test-workspace",
            root=tmp_path,
            telegram_group_id=999,
            bot_token="token",
            folders=folders,
        )

    def test_routes_to_general_for_none_thread_id(
        self, workspace_config: WorkspaceConfig
    ) -> None:
        """Test routing to General topic when thread_id is None."""
        router = WorkspaceRouter(workspace_config)
        route = router.route(None, "hello")
        assert route.is_general is True
        assert route.folder is None
        assert route.is_unbound_topic is False

    def test_routes_to_general_for_thread_id_1(
        self, workspace_config: WorkspaceConfig
    ) -> None:
        """Test routing to General topic when thread_id is 1."""
        router = WorkspaceRouter(workspace_config)
        route = router.route(1, "hello")
        assert route.is_general is True
        assert route.folder is None

    def test_routes_to_folder_by_topic_id(
        self, workspace_config: WorkspaceConfig
    ) -> None:
        """Test routing to correct folder by topic_id."""
        router = WorkspaceRouter(workspace_config)
        route = router.route(100, "hello")
        assert route.is_general is False
        assert route.folder is not None
        assert route.folder.name == "frontend"
        assert route.is_unbound_topic is False

    def test_routes_to_different_folder(
        self, workspace_config: WorkspaceConfig
    ) -> None:
        """Test routing to different folder by topic_id."""
        router = WorkspaceRouter(workspace_config)
        route = router.route(200, "hello")
        assert route.folder is not None
        assert route.folder.name == "backend"

    def test_routes_unbound_topic(self, workspace_config: WorkspaceConfig) -> None:
        """Test routing for unbound topic returns unbound flag."""
        router = WorkspaceRouter(workspace_config)
        route = router.route(999, "hello")  # Non-existent topic
        assert route.is_general is False
        assert route.folder is None
        assert route.is_unbound_topic is True

    def test_parses_slash_command_in_route(
        self, workspace_config: WorkspaceConfig
    ) -> None:
        """Test routing parses slash commands correctly."""
        router = WorkspaceRouter(workspace_config)
        route = router.route(None, "/help me")
        assert route.is_slash_command is True
        assert route.command == "help"
        assert route.command_args == "me"

    def test_non_slash_command_route(self, workspace_config: WorkspaceConfig) -> None:
        """Test routing without slash command."""
        router = WorkspaceRouter(workspace_config)
        route = router.route(None, "just a message")
        assert route.is_slash_command is False
        assert route.command is None
        assert route.command_args == "just a message"

    def test_reload_config(self, tmp_path: Path) -> None:
        """Test reloading config updates topic map."""
        initial_config = WorkspaceConfig(
            name="test",
            root=tmp_path,
            telegram_group_id=999,
            bot_token="token",
            folders={"old": FolderConfig(name="old", path="old", topic_id=100)},
        )
        router = WorkspaceRouter(initial_config)

        # Verify initial state
        route = router.route(100, "test")
        assert route.folder is not None
        assert route.folder.name == "old"

        # Update config
        new_config = WorkspaceConfig(
            name="test",
            root=tmp_path,
            telegram_group_id=999,
            bot_token="token",
            folders={"new": FolderConfig(name="new", path="new", topic_id=100)},
        )
        router.reload_config(new_config)

        # Verify new state
        route = router.route(100, "test")
        assert route.folder is not None
        assert route.folder.name == "new"

    def test_is_ralph_command(self, workspace_config: WorkspaceConfig) -> None:
        """Test is_ralph_command returns True for /ralph."""
        router = WorkspaceRouter(workspace_config)
        route = router.route(100, "/ralph do something")
        assert router.is_ralph_command(route) is True

    def test_is_ralph_command_false(self, workspace_config: WorkspaceConfig) -> None:
        """Test is_ralph_command returns False for other commands."""
        router = WorkspaceRouter(workspace_config)
        route = router.route(100, "/help")
        assert router.is_ralph_command(route) is False

    def test_should_use_ralph_explicit_command(
        self, workspace_config: WorkspaceConfig
    ) -> None:
        """Test should_use_ralph returns True for explicit /ralph command."""
        router = WorkspaceRouter(workspace_config)
        route = router.route(100, "/ralph test")  # Worker topic
        assert router.should_use_ralph(route) is True

    def test_should_use_ralph_enabled_config(self, tmp_path: Path) -> None:
        """Test should_use_ralph returns True when ralph.enabled is True."""
        config = WorkspaceConfig(
            name="test",
            root=tmp_path,
            telegram_group_id=999,
            bot_token="token",
            folders={"test": FolderConfig(name="test", path="test", topic_id=100)},
            ralph=RalphConfig(enabled=True),
        )
        router = WorkspaceRouter(config)
        route = router.route(100, "normal message")  # Worker topic, not /ralph
        assert router.should_use_ralph(route) is True

    def test_should_use_ralph_false_for_general(
        self, workspace_config: WorkspaceConfig
    ) -> None:
        """Test should_use_ralph returns False for General topic."""
        router = WorkspaceRouter(workspace_config)
        route = router.route(None, "/ralph test")  # General topic
        assert router.should_use_ralph(route) is False

    def test_should_use_ralph_false_when_disabled(
        self, workspace_config: WorkspaceConfig
    ) -> None:
        """Test should_use_ralph returns False when disabled and no command."""
        router = WorkspaceRouter(workspace_config)
        route = router.route(100, "normal message")  # Worker topic
        assert router.should_use_ralph(route) is False


class TestGeneralSlashCommands:
    """Tests for GENERAL_SLASH_COMMANDS and is_general_slash_command."""

    def test_general_commands_exist(self) -> None:
        """Test that expected commands are in GENERAL_SLASH_COMMANDS."""
        expected = {"clone", "create", "add", "list", "remove", "status", "help"}
        assert expected == GENERAL_SLASH_COMMANDS

    def test_is_general_slash_command_true(self, tmp_path: Path) -> None:
        """Test is_general_slash_command returns True for valid commands."""
        config = WorkspaceConfig(
            name="test",
            root=tmp_path,
            telegram_group_id=999,
            bot_token="token",
        )
        router = WorkspaceRouter(config)

        for cmd in GENERAL_SLASH_COMMANDS:
            route = router.route(None, f"/{cmd}")
            assert is_general_slash_command(route) is True, f"Failed for /{cmd}"

    def test_is_general_slash_command_false_for_worker(self, tmp_path: Path) -> None:
        """Test is_general_slash_command returns False for worker topics."""
        config = WorkspaceConfig(
            name="test",
            root=tmp_path,
            telegram_group_id=999,
            bot_token="token",
            folders={"test": FolderConfig(name="test", path="test", topic_id=100)},
        )
        router = WorkspaceRouter(config)
        route = router.route(100, "/help")  # Worker topic
        assert is_general_slash_command(route) is False

    def test_is_general_slash_command_false_for_unknown(self, tmp_path: Path) -> None:
        """Test is_general_slash_command returns False for unknown commands."""
        config = WorkspaceConfig(
            name="test",
            root=tmp_path,
            telegram_group_id=999,
            bot_token="token",
        )
        router = WorkspaceRouter(config)
        route = router.route(None, "/unknown")
        assert is_general_slash_command(route) is False

    def test_is_general_slash_command_false_for_non_command(
        self, tmp_path: Path
    ) -> None:
        """Test is_general_slash_command returns False for non-commands."""
        config = WorkspaceConfig(
            name="test",
            root=tmp_path,
            telegram_group_id=999,
            bot_token="token",
        )
        router = WorkspaceRouter(config)
        route = router.route(None, "just a message")
        assert is_general_slash_command(route) is False


class TestParseBranchDirective:
    """Tests for parse_branch_directive function."""

    def test_parses_simple_branch(self) -> None:
        """Test parsing a simple branch directive."""
        branch, text = parse_branch_directive("@feature-foo implement this")
        assert branch == "feature-foo"
        assert text == "implement this"

    def test_parses_branch_with_slash(self) -> None:
        """Test parsing branch with slash."""
        branch, text = parse_branch_directive("@feature/new-auth fix the bug")
        assert branch == "feature/new-auth"
        assert text == "fix the bug"

    def test_parses_branch_no_text(self) -> None:
        """Test parsing branch directive with no remaining text."""
        branch, text = parse_branch_directive("@main")
        assert branch == "main"
        assert text == ""

    def test_no_directive_returns_none(self) -> None:
        """Test text without directive returns None branch."""
        branch, text = parse_branch_directive("just some text")
        assert branch is None
        assert text == "just some text"

    def test_empty_string(self) -> None:
        """Test empty string returns None branch."""
        branch, text = parse_branch_directive("")
        assert branch is None
        assert text == ""

    def test_at_symbol_alone(self) -> None:
        """Test @ alone is not a branch directive."""
        branch, text = parse_branch_directive("@ foo")
        assert branch is None
        assert text == "@ foo"

    def test_at_in_middle_of_text(self) -> None:
        """Test @ in middle of text is not a directive."""
        branch, text = parse_branch_directive("send email to user@example.com")
        assert branch is None
        assert text == "send email to user@example.com"

    def test_strips_leading_whitespace_from_remaining(self) -> None:
        """Test remaining text has leading whitespace stripped."""
        branch, text = parse_branch_directive("@branch    lots of space")
        assert branch == "branch"
        assert text == "lots of space"

    def test_branch_with_numbers(self) -> None:
        """Test branch name with numbers."""
        branch, text = parse_branch_directive("@fix-123 debug")
        assert branch == "fix-123"
        assert text == "debug"

    def test_branch_with_underscore(self) -> None:
        """Test branch name with underscore."""
        branch, text = parse_branch_directive("@feature_new do something")
        assert branch == "feature_new"
        assert text == "do something"

    def test_branch_with_dot(self) -> None:
        """Test branch name with dot."""
        branch, text = parse_branch_directive("@v1.2.3 release")
        assert branch == "v1.2.3"
        assert text == "release"


class TestExtractContextFromText:
    """Tests for extract_context_from_text function."""

    def test_extracts_context_with_branch(self) -> None:
        """Test extracting context with branch."""
        text = "Some response\n\n`ctx: backend @ feature/auth`"
        ctx = extract_context_from_text(text)

        assert ctx is not None
        assert ctx.folder == "backend"
        assert ctx.branch == "feature/auth"

    def test_extracts_context_without_branch(self) -> None:
        """Test extracting context without branch."""
        text = "`ctx: backend`\nMore text"
        ctx = extract_context_from_text(text)

        assert ctx is not None
        assert ctx.folder == "backend"
        assert ctx.branch is None

    def test_returns_none_for_no_context(self) -> None:
        """Test returns None when no context footer."""
        text = "No context here"
        ctx = extract_context_from_text(text)
        assert ctx is None


class TestWorkspaceRouterBranchDirective:
    """Tests for WorkspaceRouter with branch directive support."""

    @pytest.fixture
    def workspace_config(self, tmp_path: Path) -> WorkspaceConfig:
        """Create a workspace config for testing."""
        folders = {
            "frontend": FolderConfig(name="frontend", path="frontend", topic_id=100),
            "backend": FolderConfig(name="backend", path="backend", topic_id=200),
        }
        return WorkspaceConfig(
            name="test-workspace",
            root=tmp_path,
            telegram_group_id=999,
            bot_token="token",
            folders=folders,
        )

    def test_parses_branch_directive_in_route(
        self, workspace_config: WorkspaceConfig
    ) -> None:
        """Test routing parses branch directive."""
        router = WorkspaceRouter(workspace_config)
        route = router.route(100, "@feature/foo implement this")

        assert route.branch == "feature/foo"
        assert route.prompt_text == "implement this"

    def test_parses_branch_with_slash_command(
        self, workspace_config: WorkspaceConfig
    ) -> None:
        """Test routing parses branch with slash command."""
        router = WorkspaceRouter(workspace_config)
        route = router.route(100, "/claude @feature/foo implement this")

        assert route.is_slash_command is True
        assert route.command == "claude"
        assert route.branch == "feature/foo"
        assert route.prompt_text == "implement this"

    def test_no_branch_in_route(self, workspace_config: WorkspaceConfig) -> None:
        """Test routing without branch directive."""
        router = WorkspaceRouter(workspace_config)
        route = router.route(100, "just a message")

        assert route.branch is None
        assert route.prompt_text == "just a message"

    def test_extracts_branch_from_reply(
        self, workspace_config: WorkspaceConfig
    ) -> None:
        """Test routing extracts branch from reply context."""
        router = WorkspaceRouter(workspace_config)
        reply_text = "Previous response\n\n`ctx: backend @ feature/auth`"
        route = router.route(200, "continue working", reply_text)

        assert route.branch == "feature/auth"

    def test_explicit_branch_overrides_reply(
        self, workspace_config: WorkspaceConfig
    ) -> None:
        """Test explicit @branch overrides reply context."""
        router = WorkspaceRouter(workspace_config)
        reply_text = "Previous response\n\n`ctx: backend @ feature/old`"
        route = router.route(200, "@feature/new do something else", reply_text)

        assert route.branch == "feature/new"

    def test_branch_in_general_topic(self, workspace_config: WorkspaceConfig) -> None:
        """Test branch directive in general topic (not typical but supported)."""
        router = WorkspaceRouter(workspace_config)
        route = router.route(None, "@main test something")

        assert route.is_general is True
        assert route.branch == "main"
