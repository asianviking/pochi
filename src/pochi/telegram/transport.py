"""Telegram transport implementing the Transport protocol."""

from __future__ import annotations


from ..transport import ChannelId, MessageRef, RenderedMessage, SendOptions
from .client import BotClient


def parse_channel_id(channel_id: ChannelId) -> tuple[int, int | None]:
    """Parse a channel ID into (chat_id, thread_id).

    Channel IDs are formatted as:
    - "telegram:{chat_id}" for general topic
    - "telegram:{chat_id}:{thread_id}" for forum topics
    """
    if not channel_id.startswith("telegram:"):
        raise ValueError(f"Invalid Telegram channel ID: {channel_id}")

    parts = channel_id.split(":", 2)
    if len(parts) == 2:
        # telegram:{chat_id}
        return int(parts[1]), None
    elif len(parts) == 3:
        # telegram:{chat_id}:{thread_id}
        return int(parts[1]), int(parts[2]) if parts[2] else None
    else:
        raise ValueError(f"Invalid Telegram channel ID format: {channel_id}")


def make_channel_id(chat_id: int, thread_id: int | None = None) -> ChannelId:
    """Create a channel ID from chat_id and optional thread_id."""
    if thread_id is not None:
        return f"telegram:{chat_id}:{thread_id}"
    return f"telegram:{chat_id}"


class TelegramTransport:
    """Telegram transport implementing the Transport protocol."""

    def __init__(self, bot: BotClient, *, chat_id: int | None = None) -> None:
        self.bot = bot
        self._default_chat_id = chat_id

    async def close(self) -> None:
        """Close the transport."""
        await self.bot.close()

    async def send(
        self,
        channel_id: ChannelId,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef | None:
        """Send a message to a channel."""
        chat_id, thread_id = parse_channel_id(channel_id)
        options = options or SendOptions()

        # Extract extra data
        entities = message.extra.get("entities")
        reply_markup = message.extra.get("reply_markup")

        # Handle reply_to
        reply_to_message_id: int | None = None
        if options.reply_to is not None:
            reply_to_message_id = int(options.reply_to.message_id)

        # Handle replace (delete old message after sending)
        replace_message_id: int | None = None
        if options.replace is not None:
            replace_message_id = int(options.replace.message_id)

        result = await self.bot.send_message(
            chat_id=chat_id,
            text=message.text,
            message_thread_id=thread_id,
            reply_to_message_id=reply_to_message_id,
            disable_notification=not options.notify,
            entities=entities,
            reply_markup=reply_markup,
            replace_message_id=replace_message_id,
        )

        if result is None:
            return None

        message_id = result.get("message_id")
        if message_id is None:
            return None

        return MessageRef(
            channel_id=channel_id,
            message_id=message_id,
            raw=result,
        )

    async def edit(
        self,
        ref: MessageRef,
        message: RenderedMessage,
        *,
        wait: bool = True,
    ) -> MessageRef | None:
        """Edit an existing message."""
        chat_id, _ = parse_channel_id(ref.channel_id)
        message_id = int(ref.message_id)

        # Extract extra data
        entities = message.extra.get("entities")
        reply_markup = message.extra.get("reply_markup")

        result = await self.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message.text,
            entities=entities,
            reply_markup=reply_markup,
            wait=wait,
        )

        if result is None:
            return None

        return MessageRef(
            channel_id=ref.channel_id,
            message_id=message_id,
            raw=result,
        )

    async def delete(self, ref: MessageRef) -> bool:
        """Delete a message."""
        chat_id, _ = parse_channel_id(ref.channel_id)
        message_id = int(ref.message_id)
        return await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
