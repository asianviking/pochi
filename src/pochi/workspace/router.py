"""Workspace-aware message routing based on Telegram topics."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..context import RunContext
from ..logging import get_logger

if TYPE_CHECKING:
    from .config import FolderConfig, WorkspaceConfig

logger = get_logger(__name__)

# Pattern to match @branch directive at the start of message
# Matches: @branch-name, @feature/foo, @fix-123
# Does not match: @ alone, @@ (escaped), or @ in middle of text
BRANCH_DIRECTIVE_RE = re.compile(r"^@([a-zA-Z0-9][a-zA-Z0-9/_.-]*)\s*")


@dataclass
class RouteResult:
    """Result of routing a message."""

    # Where the message should go
    is_general: bool  # True if General topic (orchestrator)
    folder: "FolderConfig | None"  # The folder if routed to a worker topic

    # What kind of message it is
    is_slash_command: bool  # True if starts with /
    command: str | None  # The command name if is_slash_command
    command_args: str  # The arguments after the command

    # Branch directive (@branch syntax)
    branch: str | None = None  # Branch name if @branch directive present
    prompt_text: str = ""  # Text after stripping command and branch directive

    # Error state
    is_unbound_topic: bool = False  # True if topic exists but no folder mapped


def parse_slash_command(text: str) -> tuple[str | None, str]:
    """Parse a slash command from text.

    Returns (command_name, remaining_text).
    command_name is None if text doesn't start with /.
    """
    if not text or not text.startswith("/"):
        return None, text

    lines = text.split("\n", 1)
    first_line = lines[0]
    rest = lines[1] if len(lines) > 1 else ""

    parts = first_line.split(maxsplit=1)
    command = parts[0][1:]  # Remove leading /

    # Handle @botname suffix
    if "@" in command:
        command = command.split("@", 1)[0]

    args = parts[1] if len(parts) > 1 else ""
    if rest:
        args = f"{args}\n{rest}" if args else rest

    return command, args.strip()


def parse_branch_directive(text: str) -> tuple[str | None, str]:
    """Parse a @branch directive from text.

    The @branch directive must appear at the start of the text (after any
    command has been stripped). Supports branch names with alphanumeric
    characters, slashes, hyphens, underscores, and dots.

    Examples:
        "@feat/new-feature implement this" -> ("feat/new-feature", "implement this")
        "@fix-123 debug" -> ("fix-123", "debug")
        "no directive here" -> (None, "no directive here")

    Returns:
        (branch_name, remaining_text) tuple.
        branch_name is None if no directive found.
    """
    if not text:
        return None, text

    match = BRANCH_DIRECTIVE_RE.match(text)
    if not match:
        return None, text

    branch = match.group(1)
    remaining = text[match.end() :].strip()

    return branch, remaining


def extract_context_from_text(text: str) -> RunContext | None:
    """Extract a RunContext from message text containing a ctx: footer.

    This is used to route replies back to the correct folder/branch context.

    Args:
        text: Message text that may contain a ctx: footer.

    Returns:
        RunContext if found, None otherwise.
    """
    return RunContext.parse(text)


class WorkspaceRouter:
    """Routes messages to the appropriate handler based on topic."""

    def __init__(self, config: "WorkspaceConfig") -> None:
        self.config = config
        self._topic_to_folder: dict[int, "FolderConfig"] = {}
        self._rebuild_topic_map()

    def _rebuild_topic_map(self) -> None:
        """Rebuild the topic_id -> folder mapping."""
        self._topic_to_folder.clear()
        for folder in self.config.folders.values():
            if folder.topic_id is not None:
                self._topic_to_folder[folder.topic_id] = folder

    def reload_config(self, config: "WorkspaceConfig") -> None:
        """Reload with updated config."""
        self.config = config
        self._rebuild_topic_map()

    def route(
        self, message_thread_id: int | None, text: str, reply_text: str | None = None
    ) -> RouteResult:
        """Route a message based on its topic and content.

        Args:
            message_thread_id: The Telegram message_thread_id (None for General topic)
            text: The message text
            reply_text: Text of the message being replied to (for context extraction)

        Returns:
            RouteResult with routing information
        """
        command, command_args = parse_slash_command(text)
        is_slash_command = command is not None

        # Parse @branch directive from command args (or text if no command)
        text_to_parse = command_args if is_slash_command else text
        branch, prompt_text = parse_branch_directive(text_to_parse)

        # If replying to a message with ctx: footer, extract the branch from there
        if branch is None and reply_text:
            ctx = extract_context_from_text(reply_text)
            if ctx and ctx.branch:
                branch = ctx.branch

        # General topic (message_thread_id is None or 1 for the general topic)
        # Note: Telegram uses message_thread_id=1 for General in some cases
        if message_thread_id is None or message_thread_id == 1:
            return RouteResult(
                is_general=True,
                folder=None,
                is_slash_command=is_slash_command,
                command=command,
                command_args=command_args,
                branch=branch,
                prompt_text=prompt_text,
            )

        # Specific topic - find the folder
        folder = self._topic_to_folder.get(message_thread_id)

        if folder is None:
            # Topic exists but no folder mapped
            logger.warning(
                "workspace.route.unbound_topic",
                message_thread_id=message_thread_id,
            )
            return RouteResult(
                is_general=False,
                folder=None,
                is_slash_command=is_slash_command,
                command=command,
                command_args=command_args,
                branch=branch,
                prompt_text=prompt_text,
                is_unbound_topic=True,
            )

        return RouteResult(
            is_general=False,
            folder=folder,
            is_slash_command=is_slash_command,
            command=command,
            command_args=command_args,
            branch=branch,
            prompt_text=prompt_text,
        )

    def is_ralph_command(self, route: RouteResult) -> bool:
        """Check if this is a /ralph command."""
        return route.is_slash_command and route.command == "ralph"

    def should_use_ralph(self, route: RouteResult) -> bool:
        """Check if ralph mode should be used for this message.

        Returns True if:
        - Explicit /ralph-loop command, OR
        - ralph.enabled is True in config (always-on mode)
        """
        if route.is_general:
            # Orchestrator never uses ralph
            return False

        if self.is_ralph_command(route):
            return True

        if self.config.ralph.enabled:
            # Always-on ralph mode for workers
            return True

        return False


# General topic slash commands that Python handles directly
GENERAL_SLASH_COMMANDS = {
    "clone",
    "create",
    "add",
    "list",
    "remove",
    "status",
    "help",
}


def is_general_slash_command(route: RouteResult) -> bool:
    """Check if this is a slash command that should be handled by Python."""
    if not route.is_general or not route.is_slash_command:
        return False
    return route.command in GENERAL_SLASH_COMMANDS
