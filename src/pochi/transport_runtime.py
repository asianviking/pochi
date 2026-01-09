"""TransportRuntime facade for transport plugins.

This provides a safe, stable interface for transport plugins to access
internal Pochi functionality without coupling to implementation details.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .model import EngineId, ResumeToken

if TYPE_CHECKING:
    from .config import WorkspaceConfig
    from .router import AutoRouter, RunnerEntry
    from .runner import Runner


@dataclass(frozen=True, slots=True)
class ResolvedMessage:
    """Result of resolving a message for processing."""

    # The prompt text (after stripping resume lines)
    prompt: str

    # Extracted resume token (if any)
    resume_token: ResumeToken | None = None

    # Engine override specified in message (e.g., @claude)
    engine_override: EngineId | None = None

    # Context for folder/project routing
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResolvedRunner:
    """Result of resolving a runner for execution."""

    engine: EngineId
    runner: "Runner"
    available: bool = True
    issue: str | None = None


class TransportRuntime:
    """Facade exposing safe interfaces to transport plugins.

    This decouples transport implementations from internal router/config details.
    Transport plugins should use this class to:
    - Resolve messages to prompts and resume tokens
    - Select the appropriate engine runner
    - Access folder/project configuration
    - Get plugin-specific configuration
    - Build startup messages
    """

    def __init__(
        self,
        *,
        router: "AutoRouter",
        config_path: Path | None = None,
        plugin_configs: dict[str, dict[str, Any]] | None = None,
        folder_aliases: tuple[str, ...] = (),
        workspace_config: "WorkspaceConfig | None" = None,
        available_entries: list["RunnerEntry"] | None = None,
        unavailable_entries: list["RunnerEntry"] | None = None,
    ) -> None:
        """Initialize the transport runtime.

        Args:
            router: The AutoRouter for engine resolution
            config_path: Path to the workspace config file
            plugin_configs: Dict of plugin_id -> config dict
            folder_aliases: Tuple of available folder/project aliases
            workspace_config: The full workspace configuration
            available_entries: List of available runner entries (for startup message)
            unavailable_entries: List of unavailable runner entries (for startup message)
        """
        self._router = router
        self._config_path = config_path
        self._plugin_configs = plugin_configs or {}
        self._folder_aliases = folder_aliases
        self._workspace_config = workspace_config
        self._available_entries = available_entries or []
        self._unavailable_entries = unavailable_entries or []

    @property
    def router(self) -> "AutoRouter":
        """Get the underlying router."""
        return self._router

    @property
    def config_path(self) -> Path | None:
        """Get the active config file path (if available)."""
        return self._config_path

    @property
    def workspace_config(self) -> "WorkspaceConfig | None":
        """Get the full workspace configuration."""
        return self._workspace_config

    @property
    def default_engine(self) -> EngineId:
        """Get the default engine ID."""
        return self._router.default_engine

    @property
    def engine_ids(self) -> tuple[EngineId, ...]:
        """Get all configured engine IDs (available and unavailable)."""
        return self._router.engine_ids

    def available_engine_ids(self) -> tuple[EngineId, ...]:
        """Get engine IDs that are currently available."""
        return tuple(e.engine for e in self._router.available_entries)

    def missing_engine_ids(self) -> tuple[EngineId, ...]:
        """Get engine IDs that are configured but unavailable."""
        available = set(self.available_engine_ids())
        return tuple(e for e in self.engine_ids if e not in available)

    def folder_aliases(self) -> tuple[str, ...]:
        """Get available folder/project aliases."""
        return self._folder_aliases

    def plugin_config(self, plugin_id: str) -> dict[str, Any]:
        """Get configuration for a plugin from [plugins.<id>] section.

        Args:
            plugin_id: The plugin ID

        Returns:
            Config dict, or empty dict if not configured
        """
        return self._plugin_configs.get(plugin_id, {})

    def resolve_engine(
        self,
        *,
        engine_override: EngineId | None = None,
        resume_token: ResumeToken | None = None,
    ) -> EngineId:
        """Resolve which engine to use.

        Priority:
        1. Resume token's engine (for session continuity)
        2. Explicit engine override
        3. Default engine

        Args:
            engine_override: Explicit engine requested
            resume_token: Resume token from previous message

        Returns:
            The resolved engine ID
        """
        if resume_token is not None:
            return resume_token.engine
        if engine_override is not None:
            return engine_override
        return self.default_engine

    def resolve_runner(
        self,
        *,
        resume_token: ResumeToken | None = None,
        engine_override: EngineId | None = None,
    ) -> ResolvedRunner:
        """Resolve the runner for a message.

        Args:
            resume_token: Resume token from previous message
            engine_override: Explicit engine requested

        Returns:
            ResolvedRunner with the selected runner and availability info
        """
        engine = self.resolve_engine(
            engine_override=engine_override,
            resume_token=resume_token,
        )

        entry = self._router.entry_for_engine(engine)
        return ResolvedRunner(
            engine=entry.engine,
            runner=entry.runner,
            available=entry.available,
            issue=entry.issue,
        )

    def resolve_message(
        self,
        text: str,
        reply_text: str | None = None,
    ) -> ResolvedMessage:
        """Resolve a message to a prompt and resume token.

        Args:
            text: The message text
            reply_text: Text of the message being replied to (if any)

        Returns:
            ResolvedMessage with prompt and extracted resume token
        """
        # Extract resume token from text or reply
        resume_token = self._router.resolve_resume(text, reply_text)

        # Strip resume lines from prompt
        prompt_lines = []
        for line in text.split("\n"):
            if not self._router.is_resume_line(line):
                prompt_lines.append(line)
        prompt = "\n".join(prompt_lines).strip()

        return ResolvedMessage(
            prompt=prompt,
            resume_token=resume_token,
        )

    def format_resume(self, token: ResumeToken) -> str:
        """Format a resume token for display.

        Args:
            token: The resume token

        Returns:
            Formatted string like `` `claude resume <token>` ``
        """
        return self._router.format_resume(token)

    def is_resume_line(self, line: str) -> bool:
        """Check if a line is a resume token line.

        Used to strip resume lines from prompts.

        Args:
            line: A single line of text

        Returns:
            True if the line is a resume token
        """
        return self._router.is_resume_line(line)

    def format_context_line(self, context: dict[str, Any]) -> str | None:
        """Format a context line for the prompt.

        Args:
            context: Context dict from ResolvedMessage

        Returns:
            Formatted context line, or None if no context
        """
        # Simple implementation - can be extended for project/branch context
        folder = context.get("folder")
        branch = context.get("branch")

        if folder and branch:
            return f"[{folder}@{branch}]"
        if folder:
            return f"[{folder}]"
        return None

    def build_startup_message(
        self,
        workspace_root: Path,
        final_notify: bool = True,
    ) -> str:
        """Build the startup message for the transport.

        Args:
            workspace_root: Path to the workspace root
            final_notify: Whether final_notify is enabled

        Returns:
            Formatted startup message
        """
        workspace_config = self._workspace_config

        if workspace_config is None:
            return "\N{DOG FACE} **pochi ready**"

        # Build startup message with engine info
        repo_count = len(workspace_config.folders)
        ralph_status = "enabled" if workspace_config.ralph.enabled else "on-demand"
        available_engines = [e.engine for e in self._available_entries]
        unavailable_engines = [e.engine for e in self._unavailable_entries]

        agents_line = ", ".join(f"`{e}`" for e in available_engines)
        if unavailable_engines:
            not_installed = ", ".join(f"`{e}`" for e in unavailable_engines)
            agents_line = f"{agents_line} (not installed: {not_installed})"

        return (
            f"\N{DOG FACE} **pochi ready**\n\n"
            f"workspace: `{workspace_config.name}`  \n"
            f"repos: `{repo_count}`  \n"
            f"default: `{self.default_engine}`  \n"
            f"agents: {agents_line}  \n"
            f"ralph: `{ralph_status}`  \n"
            f"working in: `{workspace_root}`"
        )
