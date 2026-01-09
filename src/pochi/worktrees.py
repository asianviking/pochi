"""Git worktree management for isolated branch execution.

This module provides worktree creation and management, enabling the @branch
directive to run agent sessions in isolated git worktrees.
"""

from __future__ import annotations

import re
from pathlib import Path

from .logging import get_logger
from .utils.git import (
    GitError,
    add_worktree,
    branch_exists,
    get_default_branch,
    is_git_repo,
    list_worktrees,
    remote_branch_exists,
    worktree_exists,
)

logger = get_logger(__name__)

# Default directory name for worktrees within a folder
DEFAULT_WORKTREES_DIR = ".worktrees"

# Pattern for valid branch names (git branch naming rules, simplified)
# Allows alphanumeric, /, -, _, .
BRANCH_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9/_.-]*$")


class WorktreeError(Exception):
    """Error during worktree operation."""

    pass


def sanitize_branch_name(name: str) -> str:
    """Sanitize a branch name for safe filesystem and git usage.

    - Strips leading/trailing whitespace
    - Replaces spaces with hyphens
    - Removes invalid characters
    - Collapses consecutive separators

    Args:
        name: Raw branch name from user input.

    Returns:
        Sanitized branch name.

    Raises:
        ValueError: If the resulting name is empty or invalid.
    """
    if not name:
        raise ValueError("Branch name cannot be empty")

    # Strip whitespace
    name = name.strip()

    # Replace spaces with hyphens
    name = name.replace(" ", "-")

    # Remove leading slashes (git doesn't like /foo)
    name = name.lstrip("/")

    # Remove .. sequences (security/git issue)
    while ".." in name:
        name = name.replace("..", ".")

    # Collapse consecutive slashes
    while "//" in name:
        name = name.replace("//", "/")

    # Collapse consecutive hyphens
    while "--" in name:
        name = name.replace("--", "-")

    # Remove trailing slashes and dots
    name = name.rstrip("/.")

    # Final validation
    if not name:
        raise ValueError("Branch name cannot be empty after sanitization")

    # Check for valid characters
    if not BRANCH_NAME_RE.match(name):
        raise ValueError(f"Invalid branch name: {name}")

    return name


def get_worktree_path(
    folder_path: Path,
    branch: str,
    worktrees_dir: str = DEFAULT_WORKTREES_DIR,
) -> Path:
    """Get the path where a worktree for a branch should be located.

    Args:
        folder_path: Path to the main repository folder.
        branch: Branch name.
        worktrees_dir: Directory name for worktrees (relative to folder).

    Returns:
        Absolute path to the worktree directory.
    """
    # Convert branch slashes to double underscores for filesystem safety
    # e.g., feature/foo -> feature__foo
    safe_name = branch.replace("/", "__")
    return folder_path / worktrees_dir / safe_name


def ensure_worktree(
    folder_path: Path,
    branch: str,
    *,
    worktrees_dir: str = DEFAULT_WORKTREES_DIR,
    base_branch: str | None = None,
) -> Path:
    """Ensure a worktree exists for the given branch, creating if needed.

    Auto-creation logic:
    1. If worktree exists → use it
    2. If local branch exists → git worktree add <path> <branch>
    3. If origin/<branch> exists → git worktree add -b <branch> <path> origin/<branch>
    4. Otherwise → create new branch from base (default branch)

    Args:
        folder_path: Path to the main repository folder.
        branch: Branch name to use/create.
        worktrees_dir: Directory name for worktrees.
        base_branch: Base branch for new branches (auto-detected if None).

    Returns:
        Path to the worktree directory.

    Raises:
        WorktreeError: If worktree creation fails.
    """
    if not is_git_repo(folder_path):
        raise WorktreeError(f"Not a git repository: {folder_path}")

    worktree_path = get_worktree_path(folder_path, branch, worktrees_dir)

    # Case 1: Worktree already exists
    if worktree_exists(worktree_path, folder_path):
        logger.info(
            "worktree.reused",
            path=str(worktree_path),
            branch=branch,
        )
        return worktree_path

    # Create parent directory if needed
    worktree_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Case 2: Local branch exists
        if branch_exists(branch, folder_path):
            logger.info(
                "worktree.creating.local_branch",
                branch=branch,
                path=str(worktree_path),
            )
            add_worktree(worktree_path, branch, folder_path)
            return worktree_path

        # Case 3: Remote branch exists
        if remote_branch_exists(branch, folder_path):
            logger.info(
                "worktree.creating.remote_branch",
                branch=branch,
                path=str(worktree_path),
            )
            add_worktree(
                worktree_path,
                branch,
                folder_path,
                create_branch=True,
                base_ref=f"origin/{branch}",
            )
            return worktree_path

        # Case 4: New branch from base
        if base_branch is None:
            base_branch = get_default_branch(folder_path)

        logger.info(
            "worktree.creating.new_branch",
            branch=branch,
            base=base_branch,
            path=str(worktree_path),
        )

        # Determine base ref - prefer origin/base if it exists
        if remote_branch_exists(base_branch, folder_path):
            base_ref = f"origin/{base_branch}"
        elif branch_exists(base_branch, folder_path):
            base_ref = base_branch
        else:
            # Last resort: HEAD
            base_ref = "HEAD"

        add_worktree(
            worktree_path,
            branch,
            folder_path,
            create_branch=True,
            base_ref=base_ref,
        )
        return worktree_path

    except GitError as e:
        raise WorktreeError(f"Failed to create worktree: {e}") from e


def find_worktree_for_path(path: Path, repo_path: Path) -> str | None:
    """Find which branch a worktree path corresponds to.

    Args:
        path: Path that might be a worktree.
        repo_path: Main repository path.

    Returns:
        Branch name if path is a worktree, None otherwise.
    """
    worktrees = list_worktrees(repo_path)
    path_str = str(path.resolve())

    for wt in worktrees:
        if wt.get("path") == path_str:
            return wt.get("branch")

    return None


def get_active_worktrees(
    folder_path: Path,
    worktrees_dir: str = DEFAULT_WORKTREES_DIR,
) -> list[tuple[str, Path]]:
    """Get all active worktrees for a folder.

    Args:
        folder_path: Path to the main repository folder.
        worktrees_dir: Directory name for worktrees.

    Returns:
        List of (branch_name, worktree_path) tuples.
    """
    worktrees_path = folder_path / worktrees_dir
    if not worktrees_path.exists():
        return []

    result: list[tuple[str, Path]] = []
    worktrees = list_worktrees(folder_path)

    for wt in worktrees:
        wt_path = wt.get("path", "")
        branch = wt.get("branch", "")
        if wt_path.startswith(str(worktrees_path)) and branch:
            result.append((branch, Path(wt_path)))

    return result
