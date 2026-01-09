"""Transport backend protocol for messaging platform plugins.

Transport backends connect Pochi to messaging platforms like Telegram, Discord, Slack, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .backends import EngineBackend
    from .transport_runtime import TransportRuntime


@dataclass(frozen=True, slots=True)
class SetupIssue:
    """An issue found during transport setup validation."""

    title: str
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SetupResult:
    """Result of transport setup validation."""

    issues: tuple[SetupIssue, ...] = field(default_factory=tuple)
    config_path: Path | None = None


class TransportBackend(Protocol):
    """Protocol for transport backend plugins.

    Transport backends are responsible for:
    - Validating configuration and onboarding users
    - Providing a lock token for preventing parallel runs
    - Starting the transport loop that handles messages

    Example implementation:

        class MyTransportBackend:
            id = "mytransport"
            description = "My custom transport"

            def check_setup(self, engine_backend, *, transport_override=None):
                # Validate config, return issues
                return SetupResult(issues=[], config_path=Path("config.toml"))

            def interactive_setup(self, *, force=False):
                # Run interactive setup wizard
                return True

            def lock_token(self, *, transport_config, config_path):
                # Return unique identifier for locking
                return transport_config.get("chat_id")

            def build_and_run(self, *, transport_config, config_path, runtime, ...):
                # Start the main message loop
                ...

        BACKEND = MyTransportBackend()
    """

    @property
    def id(self) -> str:
        """Unique identifier for this transport (e.g., 'telegram', 'discord')."""
        ...

    @property
    def description(self) -> str:
        """Human-readable description of the transport."""
        ...

    def check_setup(
        self,
        engine_backend: "EngineBackend",
        *,
        transport_override: str | None = None,
    ) -> SetupResult:
        """Validate transport configuration.

        Args:
            engine_backend: The engine backend to use
            transport_override: Optional transport-specific config override

        Returns:
            SetupResult with any issues found and the config path
        """
        ...

    def interactive_setup(self, *, force: bool) -> bool:
        """Run interactive setup wizard.

        Args:
            force: If True, run setup even if config already exists

        Returns:
            True if setup completed successfully
        """
        ...

    def lock_token(
        self,
        *,
        transport_config: dict[str, Any],
        config_path: Path,
    ) -> str | None:
        """Get a unique token for instance locking.

        This prevents multiple Pochi instances from running on the same chat.
        Return None if locking is not needed.

        Args:
            transport_config: The transport configuration dict
            config_path: Path to the config file

        Returns:
            A unique string identifier, or None for no locking
        """
        ...

    def build_and_run(
        self,
        *,
        transport_config: dict[str, Any],
        config_path: Path,
        runtime: "TransportRuntime",
        final_notify: bool,
        default_engine_override: str | None,
    ) -> None:
        """Build the transport and run the main loop.

        This is the main entry point that should:
        1. Create the transport client
        2. Connect to the messaging platform
        3. Handle incoming messages using the runtime
        4. Run until shutdown

        Args:
            transport_config: The transport configuration dict
            config_path: Path to the config file
            runtime: TransportRuntime facade for engine/folder resolution
            final_notify: Whether to send final responses as new messages
            default_engine_override: Optional engine override from CLI
        """
        ...
