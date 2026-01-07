"""Orchestrator context for the General topic Claude instance."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import WorkspaceConfig


def build_orchestrator_context(config: "WorkspaceConfig") -> str:
    """Build context string for the orchestrator Claude.

    This context is prepended to messages in the General topic to give
    Claude awareness of the workspace structure.
    """
    lines = [
        "# Pochi Workspace Context",
        "",
        f"You are the orchestrator for the **{config.name}** workspace.",
        f"Working directory: `{config.root}`",
        "",
    ]

    if config.folders:
        lines.append("## Folders")
        lines.append("")
        for name, folder in config.folders.items():
            abs_path = folder.absolute_path(config.root)
            topic_info = f"topic #{folder.topic_id}" if folder.topic_id else "no topic"
            type_info = "(git)" if folder.is_git_repo(config.root) else ""
            lines.append(f"- **{name}** {type_info} ({topic_info})")
            lines.append(f"  - Path: `{abs_path}`")
            if folder.description:
                lines.append(f"  - {folder.description}")
            if folder.origin:
                lines.append(f"  - Origin: `{folder.origin}`")
        lines.append("")
    else:
        lines.append("No folders in this workspace yet.")
        lines.append("")

    lines.extend(
        [
            "## Your Capabilities",
            "",
            "As the orchestrator, you can:",
            "- Answer questions about the workspace structure",
            "- Help users decide which folder to work in",
            "- Use `git clone` to clone repos or `mkdir` to create folders",
            "- Use the `gh` CLI for GitHub operations",
            "",
            "Each folder has its own channel (e.g., Telegram topic) with a dedicated Claude worker.",
            "Direct users to the appropriate channel for folder-specific work.",
            "",
            "## Available Slash Commands (handled automatically)",
            "",
            "- `/clone <name> <git-url> [path]` - Clone a git repo and create channel",
            "- `/create <name>` - Create a new folder (with git init by default, or --no-git)",
            "- `/add <name> <path>` - Add an existing folder to workspace",
            "- `/list` - List all folders in workspace",
            "- `/remove <name>` - Remove folder from workspace (keeps files)",
            "- `/status` - Show workspace status",
            "- `/engine [name]` - Show or set default engine",
            "- `/help` - Show help message",
            "",
        ]
    )

    return "\n".join(lines)


def prepend_orchestrator_context(
    config: "WorkspaceConfig",
    user_message: str,
) -> str:
    """Prepend workspace context to a user message for the orchestrator.

    Only used for the first message in a conversation (not resumes).
    """
    context = build_orchestrator_context(config)
    return f"{context}\n---\n\n{user_message}"
