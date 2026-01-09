"""Command backend protocol for custom /command plugins.

Command backends add custom slash command handlers to Pochi workspaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from .model import EngineId, ResumeToken
    from .transport_runtime import TransportRuntime


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Result returned by a command handler.

    This is a simple response payload. For more complex responses,
    use the executor to send multiple messages or run engines.
    """

    text: str
    parse_mode: Literal["text", "markdown", "html"] = "text"


@dataclass(frozen=True, slots=True)
class RunRequest:
    """Request to run an engine via CommandExecutor."""

    prompt: str
    engine: str | None = None  # None = use default engine
    resume: "ResumeToken | None" = None


@dataclass(slots=True)
class RunResult:
    """Result of running an engine via CommandExecutor."""

    engine: "EngineId"
    ok: bool
    answer: str
    resume: "ResumeToken | None" = None
    error: str | None = None


RunMode = Literal["emit", "capture"]


class CommandExecutor(Protocol):
    """Executor for running engines and sending messages from command handlers.

    This provides a safe interface for command plugins to:
    - Send messages to the chat
    - Run engine prompts and capture or emit results
    """

    async def send_message(
        self,
        text: str,
        *,
        parse_mode: Literal["text", "markdown", "html"] = "text",
        reply_to: int | None = None,
    ) -> int | None:
        """Send a message to the current channel.

        Args:
            text: Message text
            parse_mode: How to parse the text
            reply_to: Message ID to reply to

        Returns:
            The sent message ID, or None if sending failed
        """
        ...

    async def run_one(
        self,
        request: RunRequest,
        *,
        mode: RunMode = "emit",
    ) -> RunResult:
        """Run a single engine request.

        Args:
            request: The run request with prompt and optional engine/resume
            mode: "emit" sends progress/result to chat, "capture" collects silently

        Returns:
            RunResult with the engine output
        """
        ...

    async def run_many(
        self,
        requests: list[RunRequest],
        *,
        mode: RunMode = "emit",
        parallel: bool = False,
    ) -> list[RunResult]:
        """Run multiple engine requests.

        Args:
            requests: List of run requests
            mode: "emit" sends progress/result to chat, "capture" collects silently
            parallel: If True, run requests concurrently

        Returns:
            List of RunResults in same order as requests
        """
        ...


@dataclass(slots=True)
class CommandContext:
    """Context passed to a command handler.

    Provides access to:
    - The raw command text and parsed arguments
    - The original message and reply metadata
    - Plugin-specific configuration
    - Runtime for engine/folder resolution
    - Executor for sending messages or running engines
    """

    # The command name (without /)
    command: str

    # Arguments after the command (stripped)
    args_text: str

    # The full original message text
    raw_text: str

    # Message metadata
    message_id: int
    reply_to_message_id: int | None = None
    reply_to_text: str | None = None

    # Channel/chat info
    channel_id: str | None = None
    thread_id: int | None = None

    # Config path (if available)
    config_path: Path | None = None

    # Plugin-specific config from [plugins.<id>]
    plugin_config: dict[str, Any] = field(default_factory=dict)

    # Runtime for engine/folder resolution
    runtime: "TransportRuntime | None" = None

    # Executor for sending messages and running engines
    executor: "CommandExecutor | None" = None


class CommandBackend(Protocol):
    """Protocol for command backend plugins.

    Command backends add custom /command handlers. Commands only run when:
    - The message starts with /<command_id>
    - The command ID doesn't conflict with engine IDs, folder aliases, or reserved IDs

    Example implementation:

        class MultiCommand:
            id = "multi"
            description = "Run the prompt on every available engine"

            async def handle(self, ctx: CommandContext) -> CommandResult | None:
                prompt = ctx.args_text.strip()
                if not prompt:
                    return CommandResult(text="Usage: /multi <prompt>")

                # Get available engines from runtime
                engines = ctx.runtime.available_engine_ids()

                # Run on each engine
                requests = [
                    RunRequest(prompt=prompt, engine=engine)
                    for engine in engines
                ]
                results = await ctx.executor.run_many(
                    requests, mode="capture", parallel=True
                )

                # Format response
                blocks = []
                for result in results:
                    text = result.answer if result.ok else f"Error: {result.error}"
                    blocks.append(f"## {result.engine}\\n{text}")

                return CommandResult(text="\\n\\n".join(blocks), parse_mode="markdown")

        BACKEND = MultiCommand()

    Plugin configuration is available in ctx.plugin_config from workspace.toml:

        [plugins.multi]
        engines = ["claude", "codex"]
    """

    @property
    def id(self) -> str:
        """The command name (without /). Must match ID pattern."""
        ...

    @property
    def description(self) -> str:
        """Human-readable description shown in /help."""
        ...

    def handle(
        self,
        ctx: CommandContext,
    ) -> "Awaitable[CommandResult | None]":
        """Handle a command invocation.

        Args:
            ctx: The command context with message info and runtime access

        Returns:
            CommandResult with response text, or None to indicate no response.
            For complex responses, use ctx.executor to send multiple messages.
        """
        ...
