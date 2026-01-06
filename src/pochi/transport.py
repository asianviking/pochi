"""Transport abstraction for message delivery.

This module provides platform-agnostic interfaces for sending and editing
messages, aligned with takopi's transport architecture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeAlias

ChannelId: TypeAlias = int | str
MessageId: TypeAlias = int | str


@dataclass(frozen=True, slots=True)
class MessageRef:
    """Reference to a sent message."""

    channel_id: ChannelId
    message_id: MessageId
    raw: Any | None = field(default=None, compare=False, hash=False)


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    """A rendered message ready for delivery."""

    text: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SendOptions:
    """Options for sending a message."""

    reply_to: MessageRef | None = None
    notify: bool = True
    replace: MessageRef | None = None


class Transport(Protocol):
    """Protocol for message transport implementations.

    Implementations handle platform-specific message delivery (Telegram, Discord, etc.).
    """

    async def close(self) -> None:
        """Close the transport and release resources."""
        ...

    async def send(
        self,
        *,
        channel_id: ChannelId,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef | None:
        """Send a message to a channel.

        Args:
            channel_id: The target channel/topic ID
            message: The rendered message to send
            options: Optional send options (reply_to, notify, replace)

        Returns:
            MessageRef for the sent message, or None on failure
        """
        ...

    async def edit(
        self,
        *,
        ref: MessageRef,
        message: RenderedMessage,
        wait: bool = True,
    ) -> MessageRef | None:
        """Edit an existing message.

        Args:
            ref: Reference to the message to edit
            message: The new rendered message content
            wait: If False, fire-and-forget (don't wait for confirmation)

        Returns:
            Updated MessageRef, or None on failure
        """
        ...

    async def delete(self, *, ref: MessageRef) -> bool:
        """Delete a message.

        Args:
            ref: Reference to the message to delete

        Returns:
            True if deletion succeeded, False otherwise
        """
        ...
