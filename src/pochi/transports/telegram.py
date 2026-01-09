"""Telegram transport backend for Pochi.

This module provides the Telegram-specific transport implementation,
enabling Pochi to send and receive messages via Telegram bots.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..backends import SetupIssue
from ..transport_backend import SetupResult as PluginSetupResult
from ..transport_registry import SetupResult, TransportBackend

if TYPE_CHECKING:
    from ..backends import EngineBackend
    from ..config import WorkspaceConfig
    from ..transport_runtime import TransportRuntime


@dataclass(frozen=True, slots=True)
class TelegramTransportBackend:
    """Telegram transport backend implementation.

    This implements both the new TransportBackend protocol (for plugin discovery)
    and provides legacy compatibility with transport_registry.
    """

    id: str = "telegram"
    description: str = "Telegram bot transport for group chat interaction"

    # --- New plugin-based protocol methods ---

    def check_setup_plugin(
        self,
        engine_backend: "EngineBackend",
        *,
        transport_override: str | None = None,
    ) -> PluginSetupResult:
        """Check if Telegram is properly configured (plugin protocol).

        This is called during startup to validate configuration.
        """
        from ..config import load_workspace_config

        _ = engine_backend, transport_override

        config = load_workspace_config()
        if config is None:
            return PluginSetupResult(
                issues=(
                    SetupIssue(
                        title="No workspace configuration found",
                        lines=("Run 'pochi init' or 'pochi setup' to create one.",),
                    ),
                ),
            )

        issues: list[SetupIssue] = []

        # Check for bot token
        if not config.bot_token and not (
            config.telegram and config.telegram.bot_token
        ):
            issues.append(
                SetupIssue(
                    title="Missing bot_token",
                    lines=("Run 'pochi setup' to configure Telegram bot token.",),
                )
            )

        # Check for group ID
        if not config.telegram_group_id and not (
            config.telegram and config.telegram.chat_id
        ):
            issues.append(
                SetupIssue(
                    title="Missing chat_id",
                    lines=("Run 'pochi setup' to configure Telegram group ID.",),
                )
            )

        return PluginSetupResult(
            issues=tuple(issues),
            config_path=config.config_path() if config else None,
        )

    def interactive_setup(self, *, force: bool) -> bool:
        """Run interactive setup wizard.

        Delegates to the existing onboarding flow.
        """
        from ..onboarding import run_onboarding_sync

        _ = force
        result = run_onboarding_sync(Path.cwd())
        return result is not None

    def lock_token(
        self,
        *,
        transport_config: dict[str, Any],
        config_path: Path,
    ) -> str | None:
        """Get a unique token for instance locking.

        For Telegram, we use the chat_id to prevent multiple
        instances running on the same group.
        """
        chat_id = transport_config.get("chat_id")
        if chat_id:
            return f"telegram:{chat_id}"
        return None

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

        This is the main entry point that starts the Telegram bot loop.
        Currently delegates to the existing workspace bridge implementation.
        """
        # The actual implementation is in cli.py's _run_workspace()
        # This method is for plugin-based transports that manage their own loop.
        # For now, Telegram uses the legacy path through cli.py
        raise NotImplementedError(
            "Telegram transport currently uses legacy startup path. "
            "Use 'pochi' command instead."
        )

    # --- Legacy transport_registry protocol methods ---

    def check_setup(self, config: "WorkspaceConfig") -> SetupResult:
        """Check if Telegram is properly configured (legacy protocol).

        Args:
            config: Workspace configuration

        Returns:
            SetupResult indicating whether Telegram is ready
        """
        # Check for bot token
        if not config.bot_token and not (
            config.telegram and config.telegram.bot_token
        ):
            return SetupResult(
                ready=False,
                message="Missing bot_token. Run 'pochi setup' to configure.",
            )

        # Check for group ID
        if not config.telegram_group_id and not (
            config.telegram and config.telegram.chat_id
        ):
            return SetupResult(
                ready=False,
                message="Missing telegram_group_id. Run 'pochi setup' to configure.",
            )

        # Get actual values
        bot_token = config.telegram.bot_token if config.telegram else config.bot_token
        chat_id = (
            config.telegram.chat_id if config.telegram else config.telegram_group_id
        )

        return SetupResult(
            ready=True,
            message="Telegram configured",
            details={
                "bot_token_set": bool(bot_token),
                "chat_id": chat_id,
            },
        )

    def get_config_section(self) -> str:
        """Get the TOML section name for Telegram config.

        Returns:
            Section name ('telegram')
        """
        return "telegram"


# Create the backend instance
_backend = TelegramTransportBackend()

# Export for entrypoint discovery (new plugin system)
BACKEND = _backend

# Legacy export for backward compatibility with transport_registry
TRANSPORT: TransportBackend = _backend
