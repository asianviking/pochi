"""Tests for pochi.context module."""

from __future__ import annotations

from pathlib import Path

import pytest

from pochi.context import RunContext, resolve_run_path


class TestRunContext:
    """Tests for RunContext dataclass."""

    def test_format_footer_with_branch(self) -> None:
        """Test format_footer with branch returns ctx: folder @ branch format."""
        ctx = RunContext(folder="backend", branch="feature/auth")
        assert ctx.format_footer() == "`ctx: backend @ feature/auth`"

    def test_format_footer_without_branch(self) -> None:
        """Test format_footer without branch returns ctx: folder format."""
        ctx = RunContext(folder="backend")
        assert ctx.format_footer() == "`ctx: backend`"

    def test_parse_with_branch(self) -> None:
        """Test parsing context with branch from message text."""
        text = (
            "Some response\n\n`ctx: backend @ feature/auth`\n`claude --resume abc123`"
        )
        ctx = RunContext.parse(text)

        assert ctx is not None
        assert ctx.folder == "backend"
        assert ctx.branch == "feature/auth"

    def test_parse_without_branch(self) -> None:
        """Test parsing context without branch from message text."""
        text = "Some response\n\n`ctx: backend`\n`claude --resume abc123`"
        ctx = RunContext.parse(text)

        assert ctx is not None
        assert ctx.folder == "backend"
        assert ctx.branch is None

    def test_parse_no_context(self) -> None:
        """Test parsing returns None when no context footer found."""
        text = "Some response without context footer"
        ctx = RunContext.parse(text)
        assert ctx is None

    def test_parse_empty_string(self) -> None:
        """Test parsing empty string returns None."""
        ctx = RunContext.parse("")
        assert ctx is None

    def test_parse_strips_whitespace(self) -> None:
        """Test parsing strips whitespace from folder and branch."""
        text = "`ctx:  backend  @  feature/foo  `"
        ctx = RunContext.parse(text)

        assert ctx is not None
        assert ctx.folder == "backend"
        assert ctx.branch == "feature/foo"

    def test_parse_complex_branch_name(self) -> None:
        """Test parsing complex branch name with slashes and hyphens."""
        text = "`ctx: my-repo @ feature/user-auth_v2`"
        ctx = RunContext.parse(text)

        assert ctx is not None
        assert ctx.folder == "my-repo"
        assert ctx.branch == "feature/user-auth_v2"

    def test_frozen_dataclass(self) -> None:
        """Test that RunContext is immutable."""
        ctx = RunContext(folder="backend", branch="main")
        with pytest.raises(AttributeError):
            ctx.folder = "frontend"  # type: ignore


class TestResolveRunPath:
    """Tests for resolve_run_path function."""

    def test_without_branch(self, tmp_path: Path) -> None:
        """Test resolve_run_path returns folder path when no branch."""
        result = resolve_run_path(tmp_path, "backend", None)
        assert result == tmp_path / "backend"

    def test_with_simple_branch(self, tmp_path: Path) -> None:
        """Test resolve_run_path with simple branch name."""
        result = resolve_run_path(tmp_path, "backend", "feature-foo")
        expected = tmp_path / "backend" / ".worktrees" / "feature-foo"
        assert result == expected

    def test_with_slash_branch(self, tmp_path: Path) -> None:
        """Test resolve_run_path converts branch slashes to double underscores."""
        result = resolve_run_path(tmp_path, "backend", "feature/foo")
        expected = tmp_path / "backend" / ".worktrees" / "feature__foo"
        assert result == expected

    def test_custom_worktrees_dir(self, tmp_path: Path) -> None:
        """Test resolve_run_path with custom worktrees directory."""
        result = resolve_run_path(
            tmp_path, "backend", "feature-foo", worktrees_dir=".wt"
        )
        expected = tmp_path / "backend" / ".wt" / "feature-foo"
        assert result == expected

    def test_nested_folder_path(self, tmp_path: Path) -> None:
        """Test resolve_run_path with nested folder path."""
        result = resolve_run_path(tmp_path, "repos/backend", "main")
        expected = tmp_path / "repos/backend" / ".worktrees" / "main"
        assert result == expected
