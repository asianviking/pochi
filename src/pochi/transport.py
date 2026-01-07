"""Transport protocol and types for multi-platform message delivery."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeAlias

ChannelId: TypeAlias = str
MessageId: TypeAlias = int | str


@dataclass(frozen=True, slots=True)
class MessageRef:
    """Reference to a specific message in a channel."""

    channel_id: ChannelId
    message_id: MessageId
    raw: Any = None  # Transport-specific data


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    """A rendered message ready for transport."""

    text: str
    extra: dict[str, Any] = field(default_factory=dict)  # entities, embeds, etc.


@dataclass(frozen=True, slots=True)
class SendOptions:
    """Options for sending a message."""

    reply_to: MessageRef | None = None
    notify: bool = True
    replace: MessageRef | None = None  # Delete this message after sending


class Transport(Protocol):
    """Protocol for message transport implementations."""

    async def close(self) -> None:
        """Close the transport and release resources."""
        ...

    async def send(
        self,
        channel_id: ChannelId,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef | None:
        """Send a message to a channel."""
        ...

    async def edit(
        self,
        ref: MessageRef,
        message: RenderedMessage,
        *,
        wait: bool = True,
    ) -> MessageRef | None:
        """Edit an existing message."""
        ...

    async def delete(self, ref: MessageRef) -> bool:
        """Delete a message."""
        ...
