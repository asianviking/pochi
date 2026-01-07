"""Workspace manager for folder and channel operations."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .config import (
    WorkspaceConfig,
    add_folder_to_workspace,
    load_workspace_config,
    update_folder_topic_id,
)
from .logging import get_logger
from .telegram import BotClient
from .transport import ChannelId, MessageRef, RenderedMessage, SendOptions, Transport

if TYPE_CHECKING:
    from .config import FolderConfig
    from .router import ChannelRouter

logger = get_logger(__name__)


class WorkspaceManager:
    """Manages workspace operations like folder creation and channel binding."""

    def __init__(
        self,
        config: WorkspaceConfig,
        transport: Transport,
        *,
        bot: BotClient | None = None,  # Legacy: for Telegram-specific operations
    ) -> None:
        self.config = config
        self.transport = transport
        self._bot = bot
        self._pending_check_task: asyncio.Task | None = None
        self._channel_router: "ChannelRouter | None" = None

    def set_router(self, router: "ChannelRouter") -> None:
        """Set the channel router for reloading after config changes."""
        self._channel_router = router

    def _reload_router(self) -> None:
        """Reload the channel router with the current config."""
        if self._channel_router is not None:
            self._channel_router.reload_config(self.config)

    async def check_is_forum(self) -> bool:
        """Check if the Telegram group is a forum (has topics enabled)."""
        if self._bot is None:
            logger.warning("manager.check_forum.no_bot")
            return False

        chat = await self._bot.get_chat(self.config.telegram_group_id)
        if chat is None:
            logger.error(
                "manager.check_forum.failed",
                chat_id=self.config.telegram_group_id,
            )
            return False

        is_forum = chat.get("is_forum", False)
        logger.info(
            "manager.check_forum",
            chat_id=self.config.telegram_group_id,
            is_forum=is_forum,
            chat_type=chat.get("type"),
        )
        return is_forum

    async def create_telegram_topic(self, folder: "FolderConfig") -> int | None:
        """Create a Telegram topic for a folder.

        Returns the topic_id (message_thread_id) if successful.
        """
        if self._bot is None:
            logger.error("manager.create_topic.no_bot", folder=folder.name)
            return None

        logger.info(
            "manager.create_topic",
            folder=folder.name,
            chat_id=self.config.telegram_group_id,
        )

        result = await self._bot.create_forum_topic(
            chat_id=self.config.telegram_group_id,
            name=folder.name,
        )

        if result is None:
            logger.error(
                "manager.create_topic.failed",
                folder=folder.name,
            )
            return None

        topic_id = result.get("message_thread_id")
        if topic_id is None:
            logger.error(
                "manager.create_topic.no_thread_id",
                folder=folder.name,
                result=result,
            )
            return None

        logger.info(
            "manager.create_topic.success",
            folder=folder.name,
            topic_id=topic_id,
        )

        # Update config with the new topic_id
        update_folder_topic_id(self.config, folder.name, topic_id)

        # Add channel ID for this topic
        channel_id: ChannelId = f"telegram:{self.config.telegram_group_id}:{topic_id}"
        if channel_id not in folder.channels:
            folder.channels.append(channel_id)

        # Reload router so it picks up the new mapping
        self._reload_router()

        return topic_id

    async def process_pending_topics(self) -> list[tuple[str, int]]:
        """Create topics for all folders that have pending_topic=True.

        Returns list of (folder_name, topic_id) for successfully created topics.
        """
        # Reload config to get latest pending topics
        updated_config = load_workspace_config(self.config.root)
        if updated_config is not None:
            self.config = updated_config

        pending = self.config.get_pending_topics()
        if not pending:
            return []

        logger.info(
            "manager.process_pending_topics",
            count=len(pending),
            folders=[f.name for f in pending],
        )

        created: list[tuple[str, int]] = []
        for folder in pending:
            topic_id = await self.create_telegram_topic(folder)
            if topic_id is not None:
                created.append((folder.name, topic_id))

        return created

    async def add_folder(
        self,
        name: str,
        path: str,
        *,
        description: str | None = None,
        origin: str | None = None,
        create_topic: bool = True,
    ) -> tuple["FolderConfig", int | None]:
        """Add a folder to the workspace and optionally create its topic.

        Returns (folder_config, topic_id). topic_id is None if topic creation failed.
        """
        # Add to config (with pending_topic if we need to create one)
        folder = add_folder_to_workspace(
            self.config,
            name,
            path,
            description=description,
            origin=origin,
            pending_topic=create_topic,
        )

        topic_id: int | None = None
        if create_topic:
            topic_id = await self.create_telegram_topic(folder)

        return folder, topic_id

    async def send_to_channel(
        self,
        channel_id: ChannelId,
        message: RenderedMessage,
        *,
        reply_to: MessageRef | None = None,
        notify: bool = True,
    ) -> MessageRef | None:
        """Send a message to a channel."""
        return await self.transport.send(
            channel_id,
            message,
            SendOptions(reply_to=reply_to, notify=notify),
        )

    async def send_to_topic(
        self,
        topic_id: int | None,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        disable_notification: bool = False,
        parse_mode: str | None = None,
    ) -> dict | None:
        """Legacy: Send a message to a specific Telegram topic."""
        if self._bot is None:
            return None
        return await self._bot.send_message(
            chat_id=self.config.telegram_group_id,
            text=text,
            message_thread_id=topic_id,
            reply_to_message_id=reply_to_message_id,
            disable_notification=disable_notification,
            parse_mode=parse_mode,
        )

    async def send_unbound_topic_error(
        self,
        topic_id: int,
        reply_to_message_id: int,
    ) -> None:
        """Send an error message when a topic is not bound to a folder."""
        error_text = (
            "⚠️ This topic is not bound to a folder.\n\n"
            "To bind a folder, go to General and use:\n"
            "/add <name> <path>\n\n"
            "Or ask the Orchestrator to set up a new folder for this topic."
        )
        await self.send_to_topic(
            topic_id,
            error_text,
            reply_to_message_id=reply_to_message_id,
        )
