"""Run context for tracking folder and branch state during execution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunContext:
    """Context for a single agent run, tracking folder and optional branch.

    This context is included in message footers and used to route replies
    back to the correct worktree.
    """

    folder: str
    branch: str | None = None

    def format_footer(self) -> str:
        """Format the context as a footer line for messages.

        Returns:
            A string like "`ctx: folder @ branch`" or "`ctx: folder`".
        """
        if self.branch:
            return f"`ctx: {self.folder} @ {self.branch}`"
        return f"`ctx: {self.folder}`"

    @classmethod
    def parse(cls, text: str) -> "RunContext | None":
        """Parse a RunContext from message text containing a ctx: footer.

        Args:
            text: Message text that may contain a ctx: footer.

        Returns:
            RunContext if found, None otherwise.
        """
        # Look for `ctx: folder @ branch` or `ctx: folder`
        # Match backtick-wrapped format
        pattern = r"`ctx:\s*([^@`]+?)(?:\s*@\s*([^`]+))?`"
        match = re.search(pattern, text)
        if not match:
            return None

        folder = match.group(1).strip()
        branch = match.group(2).strip() if match.group(2) else None

        if not folder:
            return None

        return cls(folder=folder, branch=branch)


def resolve_run_path(
    workspace_root: Path,
    folder_path: str,
    branch: str | None,
    *,
    worktrees_dir: str = ".worktrees",
) -> Path:
    """Resolve the actual working directory for a run.

    Args:
        workspace_root: Absolute path to workspace root.
        folder_path: Relative path to the folder from workspace root.
        branch: Branch name (if using worktree), or None for main checkout.
        worktrees_dir: Directory name for worktrees.

    Returns:
        Absolute path to use as working directory.
    """
    folder_abs = workspace_root / folder_path

    if branch is None:
        return folder_abs

    # Convert branch slashes to double underscores for filesystem
    safe_branch = branch.replace("/", "__")
    return folder_abs / worktrees_dir / safe_branch
