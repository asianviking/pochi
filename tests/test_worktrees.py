"""Tests for pochi.worktrees module."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pochi.worktrees import (
    DEFAULT_WORKTREES_DIR,
    WorktreeError,
    ensure_worktree,
    get_active_worktrees,
    get_worktree_path,
    sanitize_branch_name,
)


class TestSanitizeBranchName:
    """Tests for sanitize_branch_name function."""

    def test_simple_name_unchanged(self) -> None:
        """Test that a simple valid name is unchanged."""
        assert sanitize_branch_name("feature-foo") == "feature-foo"

    def test_name_with_slash(self) -> None:
        """Test that names with slashes are preserved."""
        assert sanitize_branch_name("feature/new-thing") == "feature/new-thing"

    def test_strips_whitespace(self) -> None:
        """Test that leading/trailing whitespace is stripped."""
        assert sanitize_branch_name("  branch-name  ") == "branch-name"

    def test_replaces_spaces_with_hyphens(self) -> None:
        """Test that spaces are replaced with hyphens."""
        assert sanitize_branch_name("my new branch") == "my-new-branch"

    def test_removes_leading_slashes(self) -> None:
        """Test that leading slashes are removed."""
        assert sanitize_branch_name("/feature/foo") == "feature/foo"

    def test_removes_double_dots(self) -> None:
        """Test that .. sequences are collapsed."""
        assert sanitize_branch_name("foo..bar") == "foo.bar"

    def test_collapses_double_slashes(self) -> None:
        """Test that // sequences are collapsed."""
        assert sanitize_branch_name("foo//bar") == "foo/bar"

    def test_collapses_double_hyphens(self) -> None:
        """Test that -- sequences are collapsed."""
        assert sanitize_branch_name("foo--bar") == "foo-bar"

    def test_removes_trailing_slash(self) -> None:
        """Test that trailing slashes are removed."""
        assert sanitize_branch_name("feature/foo/") == "feature/foo"

    def test_removes_trailing_dot(self) -> None:
        """Test that trailing dots are removed."""
        assert sanitize_branch_name("feature.") == "feature"

    def test_empty_string_raises(self) -> None:
        """Test that empty string raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            sanitize_branch_name("")

    def test_whitespace_only_raises(self) -> None:
        """Test that whitespace-only string raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            sanitize_branch_name("   ")

    def test_invalid_characters_raise(self) -> None:
        """Test that invalid characters raise ValueError."""
        with pytest.raises(ValueError, match="Invalid branch name"):
            sanitize_branch_name("foo$bar")

    def test_name_with_underscore(self) -> None:
        """Test that underscores are allowed."""
        assert sanitize_branch_name("feature_foo") == "feature_foo"

    def test_complex_name(self) -> None:
        """Test a complex but valid branch name."""
        assert (
            sanitize_branch_name("feature/foo-bar_123.test")
            == "feature/foo-bar_123.test"
        )


class TestGetWorktreePath:
    """Tests for get_worktree_path function."""

    def test_simple_branch(self, tmp_path: Path) -> None:
        """Test path for simple branch name."""
        result = get_worktree_path(tmp_path, "feature-foo")
        expected = tmp_path / DEFAULT_WORKTREES_DIR / "feature-foo"
        assert result == expected

    def test_branch_with_slash(self, tmp_path: Path) -> None:
        """Test path for branch with slash (converted to double underscore)."""
        result = get_worktree_path(tmp_path, "feature/foo")
        expected = tmp_path / DEFAULT_WORKTREES_DIR / "feature__foo"
        assert result == expected

    def test_custom_worktrees_dir(self, tmp_path: Path) -> None:
        """Test path with custom worktrees directory."""
        result = get_worktree_path(tmp_path, "feature-foo", worktrees_dir=".wt")
        expected = tmp_path / ".wt" / "feature-foo"
        assert result == expected

    def test_nested_slash_branch(self, tmp_path: Path) -> None:
        """Test path for branch with multiple slashes."""
        result = get_worktree_path(tmp_path, "feature/user/auth")
        expected = tmp_path / DEFAULT_WORKTREES_DIR / "feature__user__auth"
        assert result == expected


class TestEnsureWorktree:
    """Tests for ensure_worktree function."""

    @pytest.fixture
    def git_repo(self, tmp_path: Path) -> Path:
        """Create a minimal git repository for testing."""
        repo_path = tmp_path / "test-repo"
        repo_path.mkdir()

        # Initialize git repo
        subprocess.run(
            ["git", "init"],
            cwd=repo_path,
            capture_output=True,
            check=True,
        )

        # Configure git user
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_path,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo_path,
            check=True,
        )

        # Create initial commit
        (repo_path / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo_path,
            check=True,
        )

        return repo_path

    def test_raises_if_not_git_repo(self, tmp_path: Path) -> None:
        """Test that non-git directory raises WorktreeError."""
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()

        with pytest.raises(WorktreeError, match="Not a git repository"):
            ensure_worktree(non_repo, "feature-foo")

    def test_creates_new_branch_worktree(self, git_repo: Path) -> None:
        """Test creating worktree for new branch."""
        result = ensure_worktree(git_repo, "feature-new")

        assert result.exists()
        assert result == git_repo / DEFAULT_WORKTREES_DIR / "feature-new"
        assert (result / "README.md").exists()

    def test_reuses_existing_worktree(self, git_repo: Path) -> None:
        """Test that existing worktree is reused."""
        # Create worktree first time
        result1 = ensure_worktree(git_repo, "feature-reuse")
        assert result1.exists()

        # Create file in worktree
        marker = result1 / "marker.txt"
        marker.write_text("test")

        # Second call should reuse
        result2 = ensure_worktree(git_repo, "feature-reuse")
        assert result2 == result1
        assert marker.exists()

    def test_creates_worktree_for_existing_branch(self, git_repo: Path) -> None:
        """Test creating worktree for existing local branch."""
        # Create branch first
        subprocess.run(
            ["git", "branch", "existing-branch"],
            cwd=git_repo,
            check=True,
        )

        result = ensure_worktree(git_repo, "existing-branch")
        assert result.exists()
        assert (result / "README.md").exists()

    def test_creates_parent_directories(self, git_repo: Path) -> None:
        """Test that parent worktrees directory is created."""
        worktrees_dir = git_repo / DEFAULT_WORKTREES_DIR
        assert not worktrees_dir.exists()

        ensure_worktree(git_repo, "feature-test")
        assert worktrees_dir.exists()

    def test_custom_worktrees_dir(self, git_repo: Path) -> None:
        """Test using custom worktrees directory."""
        result = ensure_worktree(git_repo, "feature-custom", worktrees_dir=".custom")

        expected = git_repo / ".custom" / "feature-custom"
        assert result == expected
        assert result.exists()


class TestGetActiveWorktrees:
    """Tests for get_active_worktrees function."""

    @pytest.fixture
    def git_repo_with_worktrees(self, tmp_path: Path) -> Path:
        """Create a git repo with some worktrees."""
        repo_path = tmp_path / "test-repo"
        repo_path.mkdir()

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=repo_path, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_path,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo_path,
            check=True,
        )
        (repo_path / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True
        )

        # Create worktrees
        ensure_worktree(repo_path, "feature-one")
        ensure_worktree(repo_path, "feature-two")

        return repo_path

    def test_returns_empty_for_no_worktrees(self, tmp_path: Path) -> None:
        """Test returns empty list when no worktrees exist."""
        repo_path = tmp_path / "empty-repo"
        repo_path.mkdir()
        subprocess.run(["git", "init"], cwd=repo_path, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_path,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo_path,
            check=True,
        )
        (repo_path / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True
        )

        result = get_active_worktrees(repo_path)
        assert result == []

    def test_returns_active_worktrees(self, git_repo_with_worktrees: Path) -> None:
        """Test returns list of active worktrees."""
        result = get_active_worktrees(git_repo_with_worktrees)

        assert len(result) == 2
        branches = [branch for branch, _ in result]
        assert "feature-one" in branches
        assert "feature-two" in branches
