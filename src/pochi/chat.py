"""Platform-agnostic chat abstraction layer.

This module defines the core abstractions for multi-platform chat support,
allowing Pochi to work with both Telegram and Discord (and potentially other
platforms in the future).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


Platform = Literal["telegram", "discord"]


@dataclass(frozen=True, slots=True)
class MessageRef:
    """Platform-agnostic reference to a sent message.

    This is returned after sending a message and can be used to edit or delete it.
    """

    platform: Platform
    channel_id: int  # topic_id (Telegram) or channel_id (Discord)
    message_id: int
    thread_id: int | None = None  # Discord thread ID (None for Telegram)


@dataclass(frozen=True, slots=True)
class Destination:
    """Where to send a message.

    Encapsulates the target location for sending messages across platforms.
    """

    channel_id: int  # topic_id (Telegram) or channel_id (Discord)
    thread_id: int | None = None  # Discord thread ID
    reply_to: int | None = None  # Message ID to reply to


@dataclass(slots=True)
class ChatUpdate:
    """Normalized update from any chat platform.

    This provides a unified interface for processing messages from
    Telegram or Discord.
    """

    platform: Platform
    update_type: Literal["message", "callback_query"]

    # Message fields (when update_type == "message")
    channel_id: int = 0  # group_id (Telegram) or channel_id (Discord)
    thread_id: int | None = None  # topic_id (Telegram) or thread_id (Discord)
    message_id: int = 0
    text: str = ""
    user_id: int = 0

    # Reply context
    reply_to_message_id: int | None = None
    reply_to_text: str | None = None

    # Callback query fields (when update_type == "callback_query")
    callback_query_id: str | None = None
    callback_data: str | None = None
    callback_message_id: int | None = None
    callback_chat_id: int | None = None

    # Platform-specific raw data for edge cases
    raw: dict[str, Any] = field(default_factory=dict)


class ChatProvider(Protocol):
    """Protocol for chat platform implementations.

    Both TelegramProvider and DiscordProvider implement this interface,
    allowing the workspace bridge to work with either platform.
    """

    @property
    def platform(self) -> Platform:
        """Return the platform identifier."""
        ...

    async def send_message(
        self,
        dest: Destination,
        text: str,
        *,
        entities: list[dict[str, Any]] | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        disable_notification: bool = False,
    ) -> MessageRef | None:
        """Send a message to a destination.

        Args:
            dest: Where to send the message
            text: The message text
            entities: Message formatting entities (Telegram-style)
            parse_mode: Parse mode for formatting (Markdown, HTML, etc.)
            reply_markup: Inline keyboard or other reply markup
            disable_notification: Send silently

        Returns:
            MessageRef for the sent message, or None on failure
        """
        ...

    async def edit_message(
        self,
        ref: MessageRef,
        text: str,
        *,
        entities: list[dict[str, Any]] | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
        wait: bool = True,
    ) -> bool:
        """Edit an existing message.

        Args:
            ref: Reference to the message to edit
            text: New message text
            entities: New formatting entities
            parse_mode: Parse mode for formatting
            reply_markup: New reply markup
            wait: If False, fire-and-forget (don't wait for confirmation)

        Returns:
            True if edit succeeded, False otherwise
        """
        ...

    async def delete_message(self, ref: MessageRef) -> bool:
        """Delete a message.

        Args:
            ref: Reference to the message to delete

        Returns:
            True if deletion succeeded, False otherwise
        """
        ...

    async def edit_message_reply_markup(
        self,
        ref: MessageRef,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """Edit only the reply markup of a message.

        Args:
            ref: Reference to the message to edit
            reply_markup: New reply markup (or None/empty to remove)

        Returns:
            True if edit succeeded, False otherwise
        """
        ...

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
    ) -> bool:
        """Answer a callback query (button press).

        Args:
            callback_query_id: The callback query ID to answer
            text: Optional text to show to the user

        Returns:
            True if answer succeeded, False otherwise
        """
        ...

    async def create_thread(
        self,
        channel_id: int,
        message_id: int,
        name: str,
    ) -> int | None:
        """Create a thread on a message (Discord-specific, no-op on Telegram).

        Args:
            channel_id: The channel containing the message
            message_id: The message to create a thread on
            name: Name for the thread

        Returns:
            Thread ID if created, None on failure or if not supported
        """
        ...

    async def get_updates(self) -> AsyncIterator[ChatUpdate]:
        """Get updates from the platform.

        Yields:
            ChatUpdate objects for each incoming event
        """
        ...

    async def close(self) -> None:
        """Close the provider and release resources."""
        ...


def message_ref_to_edit_key(ref: MessageRef) -> tuple[str, int, int]:
    """Create a unique key for edit deduplication."""
    return (ref.platform, ref.channel_id, ref.message_id)


def telegram_topic_to_channel_id(group_id: int, topic_id: int | None) -> int:
    """Convert Telegram group + topic to a unified channel ID.

    For Telegram, we use the group_id as the base and encode the topic
    in the destination. This is a helper for compatibility.
    """
    return group_id


def destination_for_telegram(
    group_id: int,
    topic_id: int | None,
    reply_to: int | None = None,
) -> Destination:
    """Create a Destination for Telegram.

    Args:
        group_id: The Telegram group ID
        topic_id: The forum topic ID (message_thread_id), or None for General
        reply_to: Message ID to reply to

    Returns:
        Destination configured for Telegram
    """
    # For Telegram, channel_id is the group_id, and thread_id is the topic_id
    return Destination(
        channel_id=group_id,
        thread_id=topic_id,
        reply_to=reply_to,
    )


def destination_for_discord(
    channel_id: int,
    thread_id: int | None = None,
    reply_to: int | None = None,
) -> Destination:
    """Create a Destination for Discord.

    Args:
        channel_id: The Discord channel ID
        thread_id: The thread ID (if sending to a thread)
        reply_to: Message ID to reply to

    Returns:
        Destination configured for Discord
    """
    return Destination(
        channel_id=channel_id,
        thread_id=thread_id,
        reply_to=reply_to,
    )
