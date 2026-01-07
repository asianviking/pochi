"""Command result types and registry for slash commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Protocol, TypeAlias


# Command action types - things the orchestrator can execute
@dataclass(frozen=True, slots=True)
class CreateFolder:
    """Action to create a new folder."""

    name: str
    path: str
    origin: str | None = None
    init_git: bool = True


@dataclass(frozen=True, slots=True)
class CreateChannels:
    """Action to create channels for a folder on enabled transports."""

    folder_name: str


@dataclass(frozen=True, slots=True)
class RemoveFolder:
    """Action to remove a folder from workspace."""

    name: str
    archive_channels: bool = True


@dataclass(frozen=True, slots=True)
class CancelRun:
    """Action to cancel an active run."""

    pass


@dataclass(frozen=True, slots=True)
class SetEngine:
    """Action to set the default engine."""

    engine: str


@dataclass(frozen=True, slots=True)
class StartRalph:
    """Action to start a Ralph iterative loop."""

    goal: str
    max_iterations: int = 10


@dataclass(frozen=True, slots=True)
class CloneRepo:
    """Action to clone a git repository."""

    name: str
    url: str
    path: str


CommandAction: TypeAlias = (
    CreateFolder
    | CreateChannels
    | RemoveFolder
    | CancelRun
    | SetEngine
    | StartRalph
    | CloneRepo
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Result of executing a command handler."""

    message: str | None = None
    error: str | None = None
    actions: tuple[CommandAction, ...] = ()

    @classmethod
    def ok(
        cls, message: str, actions: list[CommandAction] | None = None
    ) -> CommandResult:
        """Create a successful result with a message."""
        return cls(message=message, actions=tuple(actions) if actions else ())

    @classmethod
    def err(cls, error: str) -> CommandResult:
        """Create an error result."""
        return cls(error=error)

    @classmethod
    def action(
        cls, *actions: CommandAction, message: str | None = None
    ) -> CommandResult:
        """Create a result with actions to execute."""
        return cls(message=message, actions=actions)


# Command context for filtering
CommandContext: TypeAlias = Literal["general", "folder"]


class Command(Protocol):
    """Protocol for command implementations."""

    name: str
    description: str
    contexts: tuple[CommandContext, ...]

    def __call__(self, args: str, context: CommandContext) -> CommandResult:
        """Execute the command and return a result."""
        ...


@dataclass
class CommandInfo:
    """Information about a registered command."""

    name: str
    description: str
    handler: Callable[[str, CommandContext], CommandResult]
    contexts: tuple[CommandContext, ...]


class CommandRegistry:
    """Registry for slash commands with context filtering."""

    def __init__(self) -> None:
        self._commands: dict[str, CommandInfo] = {}

    def register(
        self,
        name: str,
        handler: Callable[[str, CommandContext], CommandResult],
        *,
        description: str,
        contexts: tuple[CommandContext, ...] = ("general", "folder"),
    ) -> None:
        """Register a command handler."""
        self._commands[name] = CommandInfo(
            name=name,
            description=description,
            handler=handler,
            contexts=contexts,
        )

    def get_command(self, name: str, context: CommandContext) -> CommandInfo | None:
        """Get a command if it's valid for the given context."""
        cmd = self._commands.get(name)
        if cmd is None:
            return None
        if context not in cmd.contexts:
            return None
        return cmd

    def list_commands(self, context: CommandContext | None = None) -> list[CommandInfo]:
        """List all commands, optionally filtered by context."""
        if context is None:
            return list(self._commands.values())
        return [cmd for cmd in self._commands.values() if context in cmd.contexts]

    def execute(
        self, name: str, args: str, context: CommandContext
    ) -> CommandResult | None:
        """Execute a command if it exists and is valid for the context."""
        cmd = self.get_command(name, context)
        if cmd is None:
            return None
        return cmd.handler(args, context)


# Default registry with built-in commands
def create_default_registry() -> CommandRegistry:
    """Create a registry with the default command set."""
    registry = CommandRegistry()

    def cmd_help(args: str, ctx: CommandContext) -> CommandResult:
        return CommandResult.ok(
            "📖 Available Commands\n\n"
            "General Topic:\n"
            "  /clone <name> <url> - Clone a git repo\n"
            "  /create <name> - Create a new folder\n"
            "  /add <name> <path> - Add existing folder\n"
            "  /list - List workspace folders\n"
            "  /remove <name> - Remove folder\n"
            "  /status - Show workspace status\n"
            "  /engine [name] - Show/set default engine\n"
            "\n"
            "Folder Topics:\n"
            "  /ralph <goal> - Start iterative loop\n"
            "  /cancel - Cancel current run\n"
        )

    def cmd_cancel(args: str, ctx: CommandContext) -> CommandResult:
        return CommandResult.action(CancelRun(), message="Cancelling...")

    def cmd_engine(args: str, ctx: CommandContext) -> CommandResult:
        engine = args.strip()
        if not engine:
            # Show current engine - orchestrator will fill in details
            return CommandResult.ok(
                "🔧 Engine Configuration\n\nUse /engine <name> to change."
            )
        return CommandResult.action(SetEngine(engine=engine))

    def cmd_clone(args: str, ctx: CommandContext) -> CommandResult:
        parts = args.split()
        if len(parts) < 2:
            return CommandResult.err(
                "Usage: /clone <name> <git-url> [path]\n\n"
                "Example: /clone backend git@github.com:user/backend.git"
            )
        name = parts[0]
        url = parts[1]
        path = parts[2] if len(parts) > 2 else name
        return CommandResult.action(
            CloneRepo(name=name, url=url, path=path),
            CreateChannels(folder_name=name),
            message=f"🔄 Cloning {url}...",
        )

    def cmd_create(args: str, ctx: CommandContext) -> CommandResult:
        parts = args.split()
        if not parts or not parts[0]:
            return CommandResult.err(
                "Usage: /create <name> [--no-git]\n\nExample: /create auth-service"
            )
        no_git = "--no-git" in parts
        name_parts = [p for p in parts if p != "--no-git"]
        if not name_parts:
            return CommandResult.err("Usage: /create <name> [--no-git]")
        name = name_parts[0]
        return CommandResult.action(
            CreateFolder(name=name, path=name, init_git=not no_git),
            CreateChannels(folder_name=name),
        )

    def cmd_add(args: str, ctx: CommandContext) -> CommandResult:
        parts = args.split()
        if len(parts) < 2:
            return CommandResult.err(
                "Usage: /add <name> <path>\n\nExample: /add frontend ~/dev/my-frontend"
            )
        name = parts[0]
        path = parts[1]
        return CommandResult.action(
            CreateFolder(name=name, path=path, init_git=False),
            CreateChannels(folder_name=name),
        )

    def cmd_list(args: str, ctx: CommandContext) -> CommandResult:
        # Orchestrator will fill in the folder list
        return CommandResult.ok(
            "📁 Workspace Folders\n\n(to be filled by orchestrator)"
        )

    def cmd_remove(args: str, ctx: CommandContext) -> CommandResult:
        parts = args.split()
        if not parts or not parts[0]:
            return CommandResult.err(
                "Usage: /remove <name>\n\n"
                "This removes the folder from config but does NOT delete files."
            )
        name = parts[0]
        return CommandResult.action(RemoveFolder(name=name))

    def cmd_status(args: str, ctx: CommandContext) -> CommandResult:
        # Orchestrator will fill in status details
        return CommandResult.ok("📊 Workspace Status\n\n(to be filled by orchestrator)")

    def cmd_ralph(args: str, ctx: CommandContext) -> CommandResult:
        if ctx != "folder":
            return CommandResult.err("Ralph can only be started in a folder topic.")
        parts = args.split()
        max_iter = 10
        goal_parts = []
        i = 0
        while i < len(parts):
            if parts[i] == "--max-iterations" and i + 1 < len(parts):
                try:
                    max_iter = int(parts[i + 1])
                except ValueError:
                    pass
                i += 2
            else:
                goal_parts.append(parts[i])
                i += 1
        goal = " ".join(goal_parts).strip()
        if not goal:
            return CommandResult.err(
                "Usage: /ralph <goal> [--max-iterations N]\n\n"
                "Example: /ralph implement user authentication"
            )
        return CommandResult.action(StartRalph(goal=goal, max_iterations=max_iter))

    # Register commands with appropriate contexts
    registry.register(
        "help",
        cmd_help,
        description="Show available commands",
        contexts=("general", "folder"),
    )
    registry.register(
        "cancel",
        cmd_cancel,
        description="Cancel current run",
        contexts=("general", "folder"),
    )
    registry.register(
        "engine",
        cmd_engine,
        description="Show/set default engine",
        contexts=("general",),
    )
    registry.register(
        "clone", cmd_clone, description="Clone a git repo", contexts=("general",)
    )
    registry.register(
        "create", cmd_create, description="Create a new folder", contexts=("general",)
    )
    registry.register(
        "add", cmd_add, description="Add existing folder", contexts=("general",)
    )
    registry.register(
        "list", cmd_list, description="List workspace folders", contexts=("general",)
    )
    registry.register(
        "remove", cmd_remove, description="Remove folder", contexts=("general",)
    )
    registry.register(
        "status", cmd_status, description="Show workspace status", contexts=("general",)
    )
    registry.register(
        "ralph", cmd_ralph, description="Start iterative loop", contexts=("folder",)
    )

    return registry
