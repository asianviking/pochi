"""Discord client implementation for Pochi.

This module provides Discord integration using discord.py, implementing
the ChatProvider protocol for platform-agnostic message handling.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import anyio
import discord
from discord import app_commands

from .chat import ChatUpdate, Destination, MessageRef, Platform
from .logging import get_logger
from .model import ResumeToken

logger = get_logger(__name__)

# Discord has a 2000 character limit for messages
DISCORD_MAX_MESSAGE_LENGTH = 2000


@dataclass
class ThreadSession:
    """Tracks a Discord thread's session state."""

    thread_id: int
    resume_token: ResumeToken | None = None
    created_at: float = 0.0


class DiscordProvider:
    """ChatProvider implementation for Discord.

    Uses discord.py to interact with Discord, handling:
    - WebSocket gateway connection
    - Message sending/editing/deleting
    - Thread creation for conversations
    - Thread-to-session token mapping
    """

    def __init__(
        self,
        token: str,
        *,
        guild_id: int,
        category_id: int,
        intents: discord.Intents | None = None,
    ) -> None:
        """Initialize the Discord provider.

        Args:
            token: Discord bot token
            guild_id: The Discord server (guild) ID
            category_id: The category ID where channels are managed
            intents: Discord intents (defaults to messages + message_content + guilds)
        """
        self._token = token
        self._guild_id = guild_id
        self._category_id = category_id

        # Set up intents
        if intents is None:
            intents = discord.Intents.default()
            intents.message_content = True
            intents.guilds = True

        # Create client
        self._client = discord.Client(intents=intents)
        self._tree = app_commands.CommandTree(self._client)

        # Thread session tracking (thread_id -> session)
        self._thread_sessions: dict[int, ThreadSession] = {}

        # Update queue for get_updates()
        self._update_queue: asyncio.Queue[ChatUpdate] = asyncio.Queue()

        # Ready event
        self._ready = anyio.Event()

        # Set up event handlers
        self._setup_handlers()

    def _setup_handlers(self) -> None:
        """Set up Discord event handlers."""

        @self._client.event
        async def on_ready() -> None:
            logger.info(
                "discord.ready",
                user=str(self._client.user),
                guild_id=self._guild_id,
            )
            self._ready.set()

        @self._client.event
        async def on_message(message: discord.Message) -> None:
            # Ignore bot's own messages
            if message.author == self._client.user:
                return

            # Filter to our guild
            if message.guild is None or message.guild.id != self._guild_id:
                return

            # Create ChatUpdate
            update = self._parse_message(message)
            await self._update_queue.put(update)

    @property
    def platform(self) -> Platform:
        """Return the platform identifier."""
        return "discord"

    @property
    def client(self) -> discord.Client:
        """Access the underlying Discord client."""
        return self._client

    @property
    def command_tree(self) -> app_commands.CommandTree:
        """Access the command tree for registering slash commands."""
        return self._tree

    @property
    def guild_id(self) -> int:
        """The Discord guild ID this provider is configured for."""
        return self._guild_id

    @property
    def category_id(self) -> int:
        """The category ID for workspace channels."""
        return self._category_id

    def get_guild(self) -> discord.Guild | None:
        """Get the guild object."""
        return self._client.get_guild(self._guild_id)

    def get_category(self) -> discord.CategoryChannel | None:
        """Get the category channel."""
        guild = self.get_guild()
        if guild is None:
            return None
        channel = guild.get_channel(self._category_id)
        if isinstance(channel, discord.CategoryChannel):
            return channel
        return None

    async def start(self) -> None:
        """Start the Discord client connection."""
        # Start client in background
        asyncio.create_task(self._client.start(self._token))
        # Wait for ready
        await self._ready.wait()
        logger.info("discord.started", guild_id=self._guild_id)

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
        """Send a message to a Discord destination."""
        # Truncate if needed
        if len(text) > DISCORD_MAX_MESSAGE_LENGTH:
            text = text[: DISCORD_MAX_MESSAGE_LENGTH - 3] + "..."

        # Find the target channel or thread
        target: discord.TextChannel | discord.Thread | None = None

        if dest.thread_id is not None:
            # Sending to a thread
            target = self._client.get_channel(dest.thread_id)
            if not isinstance(target, discord.Thread):
                target = None
        else:
            # Sending to a channel
            target = self._client.get_channel(dest.channel_id)
            if not isinstance(target, discord.TextChannel):
                target = None

        if target is None:
            logger.error(
                "discord.send_failed.channel_not_found",
                channel_id=dest.channel_id,
                thread_id=dest.thread_id,
            )
            return None

        try:
            # Get reference for reply
            reference: discord.MessageReference | None = None
            if dest.reply_to is not None:
                reference = discord.MessageReference(
                    message_id=dest.reply_to,
                    channel_id=target.id,
                )

            # Send message
            msg = await target.send(
                content=text,
                reference=reference,
                silent=disable_notification,
            )

            return MessageRef(
                platform="discord",
                channel_id=dest.channel_id,
                message_id=msg.id,
                thread_id=dest.thread_id,
            )
        except discord.HTTPException as e:
            logger.error(
                "discord.send_failed",
                error=str(e),
                channel_id=dest.channel_id,
            )
            return None

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
        """Edit an existing Discord message."""
        # Truncate if needed
        if len(text) > DISCORD_MAX_MESSAGE_LENGTH:
            text = text[: DISCORD_MAX_MESSAGE_LENGTH - 3] + "..."

        # Find the channel or thread
        channel_id = ref.thread_id if ref.thread_id is not None else ref.channel_id
        channel = self._client.get_channel(channel_id)

        if channel is None:
            return False

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return False

        try:
            msg = await channel.fetch_message(ref.message_id)
            await msg.edit(content=text)
            return True
        except discord.HTTPException as e:
            logger.error(
                "discord.edit_failed",
                error=str(e),
                message_id=ref.message_id,
            )
            return False

    async def delete_message(self, ref: MessageRef) -> bool:
        """Delete a Discord message."""
        channel_id = ref.thread_id if ref.thread_id is not None else ref.channel_id
        channel = self._client.get_channel(channel_id)

        if channel is None:
            return False

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return False

        try:
            msg = await channel.fetch_message(ref.message_id)
            await msg.delete()
            return True
        except discord.HTTPException as e:
            logger.error(
                "discord.delete_failed",
                error=str(e),
                message_id=ref.message_id,
            )
            return False

    async def edit_message_reply_markup(
        self,
        ref: MessageRef,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """Edit reply markup (buttons) - Discord uses views, not inline keyboards."""
        # Discord doesn't have the same inline keyboard concept
        # This would require converting to/from discord.ui.View
        # For now, this is a no-op
        return True

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
    ) -> bool:
        """Answer a callback query - Discord uses interactions instead."""
        # Discord handles this differently via interaction responses
        # This is a no-op for compatibility
        return True

    async def create_thread(
        self,
        channel_id: int,
        message_id: int,
        name: str,
    ) -> int | None:
        """Create a thread on a message.

        Args:
            channel_id: The channel containing the message
            message_id: The message to create a thread on
            name: Name for the thread (max 100 chars)

        Returns:
            Thread ID if created, None on failure
        """
        channel = self._client.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return None

        try:
            msg = await channel.fetch_message(message_id)
            # Truncate thread name to 100 chars (Discord limit)
            thread_name = name[:100] if len(name) > 100 else name
            thread = await msg.create_thread(name=thread_name)
            return thread.id
        except discord.HTTPException as e:
            logger.error(
                "discord.create_thread_failed",
                error=str(e),
                channel_id=channel_id,
                message_id=message_id,
            )
            return None

    async def create_channel(
        self,
        name: str,
        *,
        topic: str | None = None,
    ) -> discord.TextChannel | None:
        """Create a text channel in the workspace category.

        Args:
            name: Channel name
            topic: Optional channel topic/description

        Returns:
            The created channel, or None on failure
        """
        category = self.get_category()
        if category is None:
            logger.error(
                "discord.create_channel_failed.no_category",
                category_id=self._category_id,
            )
            return None

        try:
            channel = await category.create_text_channel(
                name=name,
                topic=topic,
            )
            logger.info(
                "discord.channel_created",
                channel_id=channel.id,
                name=name,
            )
            return channel
        except discord.HTTPException as e:
            logger.error(
                "discord.create_channel_failed",
                error=str(e),
                name=name,
            )
            return None

    def get_thread_session(self, thread_id: int) -> ThreadSession | None:
        """Get the session for a thread."""
        return self._thread_sessions.get(thread_id)

    def set_thread_session(
        self,
        thread_id: int,
        resume_token: ResumeToken | None = None,
    ) -> ThreadSession:
        """Set or update a thread's session."""
        import time

        session = self._thread_sessions.get(thread_id)
        if session is None:
            session = ThreadSession(
                thread_id=thread_id,
                resume_token=resume_token,
                created_at=time.time(),
            )
            self._thread_sessions[thread_id] = session
        else:
            session.resume_token = resume_token
        return session

    def clear_thread_session(self, thread_id: int) -> None:
        """Clear a thread's session."""
        self._thread_sessions.pop(thread_id, None)

    async def get_updates(self) -> AsyncIterator[ChatUpdate]:
        """Get updates from Discord.

        Yields:
            ChatUpdate objects for each incoming message
        """
        while True:
            try:
                # Use wait_for with timeout to allow cancellation
                update = await asyncio.wait_for(
                    self._update_queue.get(),
                    timeout=1.0,
                )
                yield update
            except asyncio.TimeoutError:
                # Check if we should continue
                await anyio.sleep(0)
                continue

    def _parse_message(self, message: discord.Message) -> ChatUpdate:
        """Parse a Discord message into a ChatUpdate."""
        # Determine if this is a thread
        thread_id: int | None = None
        channel_id = message.channel.id

        if isinstance(message.channel, discord.Thread):
            thread_id = message.channel.id
            if message.channel.parent is not None:
                channel_id = message.channel.parent.id

        # Get reply context
        reply_to_message_id: int | None = None
        reply_to_text: str | None = None
        if message.reference is not None and message.reference.message_id is not None:
            reply_to_message_id = message.reference.message_id
            # Try to get the referenced message text
            if message.reference.resolved is not None:
                if isinstance(message.reference.resolved, discord.Message):
                    reply_to_text = message.reference.resolved.content

        return ChatUpdate(
            platform="discord",
            update_type="message",
            channel_id=channel_id,
            thread_id=thread_id,
            message_id=message.id,
            text=message.content,
            user_id=message.author.id,
            reply_to_message_id=reply_to_message_id,
            reply_to_text=reply_to_text,
            raw={
                "message": message,
                "author": message.author,
                "guild": message.guild,
            },
        )

    async def sync_commands(self, guild: discord.Guild | None = None) -> None:
        """Sync slash commands to Discord.

        Args:
            guild: Specific guild to sync to (faster), or None for global sync
        """
        if guild is None:
            guild = self.get_guild()

        if guild is not None:
            self._tree.copy_global_to(guild=guild)
            await self._tree.sync(guild=guild)
            logger.info(
                "discord.commands_synced",
                guild_id=guild.id,
            )
        else:
            await self._tree.sync()
            logger.info("discord.commands_synced_globally")

    async def close(self) -> None:
        """Close the Discord client."""
        await self._client.close()
        logger.info("discord.closed")


def truncate_for_thread_name(text: str, max_length: int = 50) -> str:
    """Truncate text for use as a Discord thread name.

    Args:
        text: The text to truncate
        max_length: Maximum length (default 50, Discord max is 100)

    Returns:
        Truncated text suitable for a thread name
    """
    # Remove newlines and extra whitespace
    cleaned = " ".join(text.split())

    if len(cleaned) <= max_length:
        return cleaned

    # Truncate and add ellipsis
    return cleaned[: max_length - 3] + "..."
