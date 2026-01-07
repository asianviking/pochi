from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

from .logging import get_logger
from .model import EngineId, ResumeToken
from .runner import Runner
from .transport import ChannelId

if TYPE_CHECKING:
    from .config import FolderConfig, WorkspaceConfig

logger = get_logger(__name__)


class RunnerUnavailableError(RuntimeError):
    def __init__(self, engine: EngineId, issue: str | None = None) -> None:
        message = f"engine {engine!r} is unavailable"
        if issue:
            message = f"{message}: {issue}"
        super().__init__(message)
        self.engine = engine
        self.issue = issue


@dataclass(frozen=True, slots=True)
class RunnerEntry:
    engine: EngineId
    runner: Runner
    available: bool = True
    issue: str | None = None


class AutoRouter:
    """Router for Claude runner.

    Maintains compatibility with workspace bridge while only supporting Claude.
    """

    def __init__(
        self, entries: Iterable[RunnerEntry], default_engine: EngineId = "claude"
    ) -> None:
        self._entries = tuple(entries)
        if not self._entries:
            raise ValueError("AutoRouter requires at least one runner.")
        by_engine: dict[EngineId, RunnerEntry] = {}
        for entry in self._entries:
            if entry.engine in by_engine:
                raise ValueError(f"duplicate runner engine: {entry.engine}")
            by_engine[entry.engine] = entry
        if default_engine not in by_engine:
            raise ValueError(f"default engine {default_engine!r} is not configured")
        self._by_engine = by_engine
        self.default_engine = default_engine

    @property
    def entries(self) -> tuple[RunnerEntry, ...]:
        return self._entries

    @property
    def available_entries(self) -> tuple[RunnerEntry, ...]:
        return tuple(entry for entry in self._entries if entry.available)

    @property
    def engine_ids(self) -> tuple[EngineId, ...]:
        return tuple(entry.engine for entry in self._entries)

    @property
    def default_entry(self) -> RunnerEntry:
        return self._by_engine[self.default_engine]

    def entry_for_engine(self, engine: EngineId | None) -> RunnerEntry:
        engine = self.default_engine if engine is None else engine
        entry = self._by_engine.get(engine)
        if entry is None:
            raise RunnerUnavailableError(engine, "engine not configured")
        return entry

    def entry_for(self, resume: ResumeToken | None) -> RunnerEntry:
        if resume is None:
            return self.entry_for_engine(None)
        return self.entry_for_engine(resume.engine)

    def runner_for(self, resume: ResumeToken | None) -> Runner:
        entry = self.entry_for(resume)
        if not entry.available:
            raise RunnerUnavailableError(entry.engine, entry.issue)
        return entry.runner

    def format_resume(self, token: ResumeToken) -> str:
        entry = self.entry_for(token)
        return entry.runner.format_resume(token)

    def extract_resume(self, text: str | None) -> ResumeToken | None:
        if not text:
            return None
        for entry in self._entries:
            token = entry.runner.extract_resume(text)
            if token is not None:
                return token
        return None

    def resolve_resume(
        self, text: str | None, reply_text: str | None
    ) -> ResumeToken | None:
        token = self.extract_resume(text)
        if token is not None:
            return token
        if reply_text is None:
            return None
        return self.extract_resume(reply_text)

    def is_resume_line(self, line: str) -> bool:
        return any(entry.runner.is_resume_line(line) for entry in self._entries)


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

    # Error state
    is_unbound_topic: bool  # True if topic exists but no folder mapped


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


class ChannelRouter:
    """Routes messages to folders based on channel IDs.

    Works with the multi-transport channel ID format (e.g., "telegram:123:456").
    """

    def __init__(self, config: "WorkspaceConfig") -> None:
        self.config = config
        self._channel_to_folder: dict[ChannelId, "FolderConfig"] = {}
        self._topic_to_folder: dict[int, "FolderConfig"] = {}  # Legacy support
        self._rebuild_maps()

    def _rebuild_maps(self) -> None:
        """Rebuild the channel -> folder mappings."""
        self._channel_to_folder.clear()
        self._topic_to_folder.clear()
        for folder in self.config.folders.values():
            # New: multi-transport channels
            for channel in folder.channels:
                self._channel_to_folder[channel] = folder
            # Legacy: Telegram topic_id
            if folder.topic_id is not None:
                self._topic_to_folder[folder.topic_id] = folder

    def reload_config(self, config: "WorkspaceConfig") -> None:
        """Reload with updated config."""
        self.config = config
        self._rebuild_maps()

    def resolve_folder(self, channel_id: ChannelId) -> "FolderConfig | None":
        """Get the folder for a channel ID, or None for General."""
        return self._channel_to_folder.get(channel_id)

    def resolve_folder_by_topic(self, topic_id: int) -> "FolderConfig | None":
        """Legacy: Get the folder for a Telegram topic ID."""
        return self._topic_to_folder.get(topic_id)

    def route(self, channel_id: ChannelId, text: str) -> RouteResult:
        """Route a message based on its channel and content."""
        command, command_args = parse_slash_command(text)
        is_slash_command = command is not None

        # Check if this is a folder channel
        folder = self.resolve_folder(channel_id)
        if folder is not None:
            return RouteResult(
                is_general=False,
                folder=folder,
                is_slash_command=is_slash_command,
                command=command,
                command_args=command_args,
                is_unbound_topic=False,
            )

        # Check for legacy telegram topic_id format
        # Channel format: telegram:{chat_id}:{topic_id}
        if channel_id.startswith("telegram:"):
            parts = channel_id.split(":")
            if len(parts) == 3 and parts[2]:
                try:
                    topic_id = int(parts[2])
                    folder = self.resolve_folder_by_topic(topic_id)
                    if folder is not None:
                        return RouteResult(
                            is_general=False,
                            folder=folder,
                            is_slash_command=is_slash_command,
                            command=command,
                            command_args=command_args,
                            is_unbound_topic=False,
                        )
                    # Topic exists but not mapped - unbound
                    if topic_id != 1:  # topic_id=1 is often General
                        logger.warning(
                            "router.unbound_topic",
                            channel_id=channel_id,
                            topic_id=topic_id,
                        )
                        return RouteResult(
                            is_general=False,
                            folder=None,
                            is_slash_command=is_slash_command,
                            command=command,
                            command_args=command_args,
                            is_unbound_topic=True,
                        )
                except ValueError:
                    pass

        # Default: General topic
        return RouteResult(
            is_general=True,
            folder=None,
            is_slash_command=is_slash_command,
            command=command,
            command_args=command_args,
            is_unbound_topic=False,
        )

    def is_ralph_command(self, route: RouteResult) -> bool:
        """Check if this is a /ralph command."""
        return route.is_slash_command and route.command == "ralph"

    def should_use_ralph(self, route: RouteResult) -> bool:
        """Check if ralph mode should be used for this message."""
        if route.is_general:
            return False
        if self.is_ralph_command(route):
            return True
        if self.config.ralph.enabled:
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
    "engine",
}


def is_general_slash_command(route: RouteResult) -> bool:
    """Check if this is a slash command that should be handled by Python."""
    if not route.is_general or not route.is_slash_command:
        return False
    return route.command in GENERAL_SLASH_COMMANDS
