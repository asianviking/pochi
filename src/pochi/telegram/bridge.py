"""Telegram-specific bridge for polling and update handling."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import anyio

from ..logging import get_logger
from ..transport import ChannelId
from .client import BotClient
from .transport import make_channel_id

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TelegramUpdate:
    """A normalized Telegram update."""

    channel_id: ChannelId
    message_id: int
    text: str
    reply_to_message_id: int | None
    reply_to_text: str | None
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TelegramCallbackQuery:
    """A callback query from an inline button."""

    query_id: str
    data: str
    channel_id: ChannelId
    message_id: int | None
    raw: dict[str, Any]


async def drain_backlog(
    bot: BotClient,
    offset: int | None,
) -> int | None:
    """Drain any pending updates from before we started."""
    drained = 0
    while True:
        updates = await bot.get_updates(
            offset=offset, timeout_s=0, allowed_updates=["message"]
        )
        if updates is None:
            logger.info("startup.backlog.failed")
            return offset
        logger.debug("startup.backlog.updates", updates=updates)
        if not updates:
            if drained:
                logger.info("startup.backlog.drained", count=drained)
            return offset
        offset = updates[-1]["update_id"] + 1
        drained += len(updates)


async def poll_updates(
    bot: BotClient,
    *,
    chat_id: int,
    offset: int | None = None,
    drain_on_start: bool = True,
) -> AsyncIterator[TelegramUpdate | TelegramCallbackQuery]:
    """Poll for Telegram updates, filtering to a specific chat.

    Yields TelegramUpdate for text messages and TelegramCallbackQuery for
    inline button presses.
    """
    if drain_on_start:
        offset = await drain_backlog(bot, offset)

    while True:
        updates = await bot.get_updates(
            offset=offset, timeout_s=50, allowed_updates=["message", "callback_query"]
        )
        if updates is None:
            logger.info("poll.get_updates.failed")
            await anyio.sleep(2)
            continue
        logger.debug("poll.updates", updates=updates)

        for upd in updates:
            offset = upd["update_id"] + 1

            # Handle callback queries
            callback_query = upd.get("callback_query")
            if callback_query is not None:
                msg = callback_query.get("message", {})
                msg_chat = msg.get("chat", {})
                msg_chat_id = msg_chat.get("id")
                if msg_chat_id != chat_id:
                    continue
                thread_id = msg.get("message_thread_id")
                yield TelegramCallbackQuery(
                    query_id=callback_query["id"],
                    data=callback_query.get("data", ""),
                    channel_id=make_channel_id(msg_chat_id, thread_id),
                    message_id=msg.get("message_id"),
                    raw=callback_query,
                )
                continue

            # Handle messages
            msg = upd.get("message")
            if msg is None:
                continue
            if "text" not in msg:
                continue
            if msg["chat"]["id"] != chat_id:
                continue

            thread_id = msg.get("message_thread_id")
            reply = msg.get("reply_to_message")

            yield TelegramUpdate(
                channel_id=make_channel_id(chat_id, thread_id),
                message_id=msg["message_id"],
                text=msg["text"],
                reply_to_message_id=reply.get("message_id") if reply else None,
                reply_to_text=reply.get("text") if reply else None,
                raw=msg,
            )


def build_bot_commands(
    engine_ids: tuple[str, ...],
    workspace_commands: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Build the command menu for the bot."""
    commands: list[dict[str, str]] = []
    seen: set[str] = set()

    # Add engine commands
    for engine_id in engine_ids:
        cmd = engine_id.lower()
        if cmd in seen:
            continue
        commands.append({"command": cmd, "description": f"start {cmd}"})
        seen.add(cmd)

    # Add workspace commands
    if workspace_commands:
        for cmd, desc in workspace_commands.items():
            if cmd not in seen:
                commands.append({"command": cmd, "description": desc})
                seen.add(cmd)

    # Add cancel
    if "cancel" not in seen:
        commands.append({"command": "cancel", "description": "cancel current run"})

    return commands


async def set_command_menu(
    bot: BotClient,
    engine_ids: tuple[str, ...],
    workspace_commands: dict[str, str] | None = None,
) -> None:
    """Set the bot's command menu."""
    commands = build_bot_commands(engine_ids, workspace_commands)
    if not commands:
        return
    try:
        ok = await bot.set_my_commands(commands)
    except Exception as exc:
        logger.info(
            "command_menu.failed",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        return
    if not ok:
        logger.info("command_menu.rejected")
        return
    logger.info(
        "command_menu.updated",
        commands=[cmd["command"] for cmd in commands],
    )


def is_cancel_command(text: str) -> bool:
    """Check if text is a /cancel command."""
    stripped = text.strip()
    if not stripped:
        return False
    command = stripped.split(maxsplit=1)[0]
    return command == "/cancel" or command.startswith("/cancel@")
