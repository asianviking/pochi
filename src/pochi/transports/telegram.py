"""Telegram transport backend for Pochi.

This module provides the Telegram-specific transport implementation,
enabling Pochi to send and receive messages via Telegram bots.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..transport_registry import SetupResult, TransportBackend

if TYPE_CHECKING:
    from ..config import WorkspaceConfig


@dataclass(frozen=True, slots=True)
class TelegramTransportBackend:
    """Telegram transport backend implementation."""

    id: str = "telegram"
    description: str = "Telegram bot transport for group chat interaction"

    def check_setup(self, config: "WorkspaceConfig") -> SetupResult:
        """Check if Telegram is properly configured.

        Args:
            config: Workspace configuration

        Returns:
            SetupResult indicating whether Telegram is ready
        """
        # Check for bot token
        if not config.bot_token and not (config.telegram and config.telegram.bot_token):
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


# Export the backend instance for discovery
TRANSPORT: TransportBackend = TelegramTransportBackend()
