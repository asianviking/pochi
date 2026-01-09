"""Git utility functions for working with repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..logging import get_logger

logger = get_logger(__name__)


class GitError(Exception):
    """Error from a git operation."""

    def __init__(self, message: str, returncode: int = 1, stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


def run_git(
    *args: str,
    cwd: Path | None = None,
    timeout: float = 60.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result.

    Args:
        *args: Git command arguments (without 'git' prefix).
        cwd: Working directory for the command.
        timeout: Command timeout in seconds.
        check: If True, raise GitError on non-zero exit.

    Returns:
        CompletedProcess with stdout/stderr.

    Raises:
        GitError: If check=True and command fails.
    """
    cmd = ["git", *args]
    logger.debug("git.run", cmd=cmd, cwd=str(cwd) if cwd else None)

    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if check and result.returncode != 0:
        raise GitError(
            f"git {args[0]} failed: {result.stderr.strip() or result.stdout.strip()}",
            returncode=result.returncode,
            stderr=result.stderr,
        )

    return result


def is_git_repo(path: Path) -> bool:
    """Check if a path is inside a git repository."""
    git_dir = path / ".git"
    return git_dir.exists()


def get_current_branch(cwd: Path) -> str | None:
    """Get the current branch name, or None if detached/not a repo."""
    try:
        result = run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
        branch = result.stdout.strip()
        return branch if branch and branch != "HEAD" else None
    except (GitError, subprocess.TimeoutExpired):
        return None


def get_default_branch(cwd: Path) -> str:
    """Get the default branch to use as base for new branches.

    Checks in order: origin/HEAD, origin/main, origin/master, current branch.
    Falls back to "main" if nothing found.
    """
    # Try origin/HEAD
    try:
        result = run_git(
            "symbolic-ref", "refs/remotes/origin/HEAD", cwd=cwd, check=False
        )
        if result.returncode == 0:
            ref = result.stdout.strip()
            # refs/remotes/origin/main -> main
            if ref.startswith("refs/remotes/origin/"):
                return ref.split("/")[-1]
    except subprocess.TimeoutExpired:
        pass

    # Try common default branches
    for branch in ("main", "master"):
        try:
            result = run_git(
                "rev-parse", "--verify", f"origin/{branch}", cwd=cwd, check=False
            )
            if result.returncode == 0:
                return branch
        except subprocess.TimeoutExpired:
            continue

    # Fall back to current branch
    current = get_current_branch(cwd)
    if current:
        return current

    return "main"


def branch_exists(branch: str, cwd: Path) -> bool:
    """Check if a local branch exists."""
    result = run_git("rev-parse", "--verify", branch, cwd=cwd, check=False)
    return result.returncode == 0


def remote_branch_exists(branch: str, cwd: Path, remote: str = "origin") -> bool:
    """Check if a remote branch exists."""
    result = run_git(
        "rev-parse", "--verify", f"{remote}/{branch}", cwd=cwd, check=False
    )
    return result.returncode == 0


def list_worktrees(cwd: Path) -> list[dict[str, str]]:
    """List all worktrees for a repository.

    Returns:
        List of dicts with 'path', 'commit', and 'branch' keys.
    """
    try:
        result = run_git("worktree", "list", "--porcelain", cwd=cwd)
    except GitError:
        return []

    worktrees: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for line in result.stdout.splitlines():
        if not line:
            if current:
                worktrees.append(current)
                current = {}
            continue

        if line.startswith("worktree "):
            current["path"] = line[9:]
        elif line.startswith("HEAD "):
            current["commit"] = line[5:]
        elif line.startswith("branch "):
            # refs/heads/feature/foo -> feature/foo
            ref = line[7:]
            if ref.startswith("refs/heads/"):
                current["branch"] = ref[11:]
            else:
                current["branch"] = ref
        elif line == "detached":
            current["branch"] = ""

    if current:
        worktrees.append(current)

    return worktrees


def worktree_exists(worktree_path: Path, cwd: Path) -> bool:
    """Check if a worktree exists at the given path."""
    worktrees = list_worktrees(cwd)
    worktree_str = str(worktree_path.resolve())
    return any(wt.get("path") == worktree_str for wt in worktrees)


def add_worktree(
    worktree_path: Path,
    branch: str,
    cwd: Path,
    *,
    create_branch: bool = False,
    base_ref: str | None = None,
    timeout: float = 120.0,
) -> None:
    """Add a new worktree.

    Args:
        worktree_path: Path where the worktree should be created.
        branch: Branch name to checkout in the worktree.
        cwd: Repository root path.
        create_branch: If True, create the branch with -b flag.
        base_ref: Base ref for new branch (used with create_branch).
        timeout: Command timeout in seconds.
    """
    args = ["worktree", "add"]

    if create_branch:
        args.extend(["-b", branch])
        args.append(str(worktree_path))
        if base_ref:
            args.append(base_ref)
    else:
        args.append(str(worktree_path))
        args.append(branch)

    run_git(*args, cwd=cwd, timeout=timeout)
    logger.info(
        "git.worktree.added",
        path=str(worktree_path),
        branch=branch,
        created=create_branch,
    )


def remove_worktree(worktree_path: Path, cwd: Path, *, force: bool = False) -> None:
    """Remove a worktree.

    Args:
        worktree_path: Path to the worktree to remove.
        cwd: Repository root path.
        force: If True, force removal even if dirty.
    """
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree_path))

    run_git(*args, cwd=cwd)
    logger.info("git.worktree.removed", path=str(worktree_path))
