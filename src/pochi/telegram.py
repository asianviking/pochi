"""Telegram client - re-exports from pochi.telegram package for backwards compatibility."""

# Re-export everything from the telegram package submodules directly
# to avoid the telegram.py / telegram/ naming conflict
from pochi.telegram.client import (
    BotClient,
    DELETE_PRIORITY,
    EDIT_PRIORITY,
    OutboxOp,
    RetryAfter,
    SEND_PRIORITY,
    TelegramClient,
    TelegramOutbox,
    TelegramRetryAfter,
    is_group_chat_id,
    retry_after_from_payload,
)

__all__ = [
    "BotClient",
    "DELETE_PRIORITY",
    "EDIT_PRIORITY",
    "OutboxOp",
    "RetryAfter",
    "SEND_PRIORITY",
    "TelegramClient",
    "TelegramOutbox",
    "TelegramRetryAfter",
    "is_group_chat_id",
    "retry_after_from_payload",
]
