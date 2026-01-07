"""Telegram bot client with queue-based outbox for rate limiting."""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Hashable, Protocol

import anyio
from anyio.abc import TaskGroup
import httpx

from ..logging import get_logger

logger = get_logger(__name__)


# Priority constants for the outbox queue
# Lower priority = processed first
SEND_PRIORITY = 0
DELETE_PRIORITY = 1
EDIT_PRIORITY = 2


class RetryAfter(Exception):
    """Base exception for retry-after errors."""

    def __init__(self, retry_after: float, description: str | None = None) -> None:
        super().__init__(description or f"retry after {retry_after}")
        self.retry_after = float(retry_after)
        self.description = description


class TelegramRetryAfter(RetryAfter):
    """Telegram-specific retry-after exception (429 response)."""

    pass


def is_group_chat_id(chat_id: int) -> bool:
    """Check if chat_id represents a group chat (negative IDs)."""
    return chat_id < 0


@dataclass(slots=True)
class OutboxOp:
    """A queued operation for the Telegram outbox."""

    execute: Callable[[], Awaitable[Any]]
    priority: int
    queued_at: float
    updated_at: float
    chat_id: int | None
    label: str | None = None
    done: anyio.Event = field(default_factory=anyio.Event)
    result: Any = None

    def set_result(self, result: Any) -> None:
        if self.done.is_set():
            return
        self.result = result
        self.done.set()


class TelegramOutbox:
    """Queue-based outbox for Telegram operations with priority and deduplication.

    Features:
    - Priority ordering: sends > deletes > edits
    - Edit coalescing: newer edits replace pending ones for the same message
    - Delete optimization: deletes drop pending edits for the same message
    - Global retry handling: retry_at pauses all requests
    - Non-blocking edits: wait parameter to fire-and-forget
    """

    def __init__(
        self,
        *,
        interval_for_chat: Callable[[int | None], float],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        on_error: Callable[[OutboxOp, Exception], None] | None = None,
        on_outbox_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._interval_for_chat = interval_for_chat
        self._clock = clock
        self._sleep = sleep
        self._on_error = on_error
        self._on_outbox_error = on_outbox_error
        self._pending: dict[Hashable, OutboxOp] = {}
        self._cond = anyio.Condition()
        self._start_lock = anyio.Lock()
        self._closed = False
        self._tg: TaskGroup | None = None
        self.next_at = 0.0
        self.retry_at = 0.0

    async def ensure_worker(self) -> None:
        """Start the worker task if not already running."""
        async with self._start_lock:
            if self._tg is not None or self._closed:
                return
            self._tg = await anyio.create_task_group().__aenter__()
            self._tg.start_soon(self.run)

    async def enqueue(self, *, key: Hashable, op: OutboxOp, wait: bool = True) -> Any:
        """Enqueue an operation for processing.

        Args:
            key: Unique key for deduplication (newer ops replace pending ones)
            op: The operation to queue
            wait: If True, wait for the operation to complete

        Returns:
            The result of the operation, or None if wait=False or closed
        """
        await self.ensure_worker()
        async with self._cond:
            if self._closed:
                op.set_result(None)
                return op.result
            previous = self._pending.get(key)
            if previous is not None:
                op.queued_at = previous.queued_at
                previous.set_result(None)
            else:
                op.queued_at = op.updated_at
            self._pending[key] = op
            self._cond.notify()
        if not wait:
            return None
        await op.done.wait()
        return op.result

    async def drop_pending(self, *, key: Hashable) -> None:
        """Drop a pending operation by key (e.g., when deleting a message)."""
        async with self._cond:
            pending = self._pending.pop(key, None)
            if pending is not None:
                pending.set_result(None)
            self._cond.notify()

    async def close(self) -> None:
        """Close the outbox and cancel all pending operations."""
        async with self._cond:
            self._closed = True
            self.fail_pending()
            self._cond.notify_all()
        if self._tg is not None:
            try:
                self._tg.cancel_scope.cancel()
                await self._tg.__aexit__(None, None, None)
            except RuntimeError:
                # May fail if called from a different task context
                # The worker will exit naturally when it sees _closed
                pass
            self._tg = None

    def fail_pending(self) -> None:
        """Fail all pending operations."""
        for pending in list(self._pending.values()):
            pending.set_result(None)
        self._pending.clear()

    def pick_locked(self) -> tuple[Hashable, OutboxOp] | None:
        """Pick the next operation to execute (must be called under lock)."""
        if not self._pending:
            return None
        return min(
            self._pending.items(),
            key=lambda item: (item[1].priority, item[1].queued_at),
        )

    async def execute_op(self, op: OutboxOp) -> Any:
        """Execute an operation with error handling."""
        try:
            return await op.execute()
        except Exception as exc:
            if isinstance(exc, RetryAfter):
                raise
            if self._on_error is not None:
                self._on_error(op, exc)
            return None

    async def sleep_until(self, deadline: float) -> None:
        """Sleep until the specified deadline."""
        delay = deadline - self._clock()
        if delay > 0:
            await self._sleep(delay)

    async def run(self) -> None:
        """Main worker loop that processes queued operations."""
        cancel_exc = anyio.get_cancelled_exc_class()
        try:
            while True:
                async with self._cond:
                    while not self._pending and not self._closed:
                        await self._cond.wait()
                    if self._closed and not self._pending:
                        return
                blocked_until = max(self.next_at, self.retry_at)
                if self._clock() < blocked_until:
                    await self.sleep_until(blocked_until)
                    continue
                async with self._cond:
                    if self._closed and not self._pending:
                        return
                    picked = self.pick_locked()
                    if picked is None:
                        continue
                    key, op = picked
                    self._pending.pop(key, None)
                started_at = self._clock()
                try:
                    result = await self.execute_op(op)
                except RetryAfter as exc:
                    self.retry_at = max(self.retry_at, self._clock() + exc.retry_after)
                    async with self._cond:
                        if self._closed:
                            op.set_result(None)
                        elif key not in self._pending:
                            self._pending[key] = op
                            self._cond.notify()
                        else:
                            op.set_result(None)
                    continue
                self.next_at = started_at + self._interval_for_chat(op.chat_id)
                op.set_result(result)
        except cancel_exc:
            return
        except Exception as exc:
            async with self._cond:
                self._closed = True
                self.fail_pending()
                self._cond.notify_all()
            if self._on_outbox_error is not None:
                self._on_outbox_error(exc)
            return


def retry_after_from_payload(payload: dict[str, Any]) -> float | None:
    """Extract retry_after value from a Telegram API error payload."""
    params = payload.get("parameters")
    if isinstance(params, dict):
        retry_after = params.get("retry_after")
        if isinstance(retry_after, (int, float)):
            return float(retry_after)
    return None


class BotClient(Protocol):
    async def close(self) -> None: ...

    async def get_updates(
        self,
        offset: int | None,
        timeout_s: int = 50,
        allowed_updates: list[str] | None = None,
    ) -> list[dict] | None: ...

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        disable_notification: bool | None = False,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        message_thread_id: int | None = None,
        reply_markup: dict | None = None,
        *,
        replace_message_id: int | None = None,
    ) -> dict | None: ...

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
        *,
        wait: bool = True,
    ) -> dict | None: ...

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
    ) -> bool: ...

    async def delete_message(self, chat_id: int, message_id: int) -> bool: ...

    async def edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: dict | None = None,
    ) -> dict | None: ...

    async def set_my_commands(
        self,
        commands: list[dict[str, Any]],
        *,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> bool: ...

    async def get_me(self) -> dict | None: ...

    async def get_chat(self, chat_id: int) -> dict | None: ...

    async def create_forum_topic(
        self,
        chat_id: int,
        name: str,
        icon_color: int | None = None,
        icon_custom_emoji_id: str | None = None,
    ) -> dict | None: ...

    async def close_forum_topic(self, chat_id: int, message_thread_id: int) -> bool: ...

    async def reopen_forum_topic(
        self, chat_id: int, message_thread_id: int
    ) -> bool: ...

    async def delete_forum_topic(
        self, chat_id: int, message_thread_id: int
    ) -> bool: ...


class TelegramClient:
    """Telegram bot client with queue-based outbox for rate limiting.

    Uses a priority queue to order operations:
    - Sends have highest priority (0)
    - Deletes have medium priority (1)
    - Edits have lowest priority (2)

    Edits to the same message are coalesced (only the latest is sent).
    Deletes automatically drop pending edits for the same message.
    """

    def __init__(
        self,
        token: str,
        *,
        timeout_s: float = 120,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        private_chat_rps: float = 1.0,
        group_chat_rps: float = 20.0 / 60.0,
    ) -> None:
        if not token:
            raise ValueError("Telegram token is empty")
        self._base = f"https://api.telegram.org/bot{token}"
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._owns_client = client is None
        self._clock = clock
        self._sleep = sleep
        self._private_interval = (
            0.0 if private_chat_rps <= 0 else 1.0 / private_chat_rps
        )
        self._group_interval = 0.0 if group_chat_rps <= 0 else 1.0 / group_chat_rps
        self._outbox = TelegramOutbox(
            interval_for_chat=self.interval_for_chat,
            clock=clock,
            sleep=sleep,
            on_error=self._log_request_error,
            on_outbox_error=self._log_outbox_failure,
        )
        self._seq = itertools.count()

    def interval_for_chat(self, chat_id: int | None) -> float:
        """Get the rate limit interval for a chat type."""
        if chat_id is None:
            return self._private_interval
        if is_group_chat_id(chat_id):
            return self._group_interval
        return self._private_interval

    def _log_request_error(self, op: OutboxOp, exc: Exception) -> None:
        logger.error(
            "telegram.outbox.request_failed",
            method=op.label,
            error=str(exc),
            error_type=exc.__class__.__name__,
        )

    def _log_outbox_failure(self, exc: Exception) -> None:
        logger.error(
            "telegram.outbox.failed",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )

    async def drop_pending_edits(self, *, chat_id: int, message_id: int) -> None:
        """Drop pending edits for a message (called when deleting)."""
        await self._outbox.drop_pending(key=("edit", chat_id, message_id))

    def _unique_key(self, prefix: str) -> tuple[str, int]:
        """Generate a unique key for non-deduplicating operations."""
        return (prefix, next(self._seq))

    async def _enqueue_op(
        self,
        *,
        key: Hashable,
        label: str,
        execute: Callable[[], Awaitable[Any]],
        priority: int,
        chat_id: int | None,
        wait: bool = True,
    ) -> Any:
        """Enqueue an operation for processing."""
        op = OutboxOp(
            execute=execute,
            priority=priority,
            queued_at=0.0,
            updated_at=self._clock(),
            chat_id=chat_id,
            label=label,
        )
        return await self._outbox.enqueue(key=key, op=op, wait=wait)

    async def close(self) -> None:
        await self._outbox.close()
        if self._owns_client:
            await self._client.aclose()

    async def _post(self, method: str, json_data: dict[str, Any]) -> Any | None:
        """Make a POST request to the Telegram API.

        Raises TelegramRetryAfter on 429 errors for the outbox to handle.
        """
        logger.debug("telegram.request", method=method, payload=json_data)
        try:
            resp = await self._client.post(f"{self._base}/{method}", json=json_data)
        except httpx.HTTPError as e:
            url = getattr(e.request, "url", None)
            logger.error(
                "telegram.network_error",
                method=method,
                url=str(url) if url is not None else None,
                error=str(e),
                error_type=e.__class__.__name__,
            )
            return None

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if resp.status_code == 429:
                retry_after: float | None = None
                try:
                    payload = resp.json()
                except Exception:
                    payload = None
                if isinstance(payload, dict):
                    retry_after = retry_after_from_payload(payload)
                retry_after = 5.0 if retry_after is None else retry_after
                logger.warning(
                    "telegram.rate_limited",
                    method=method,
                    status=resp.status_code,
                    url=str(resp.request.url),
                    retry_after=retry_after,
                )
                raise TelegramRetryAfter(retry_after) from e
            body = resp.text
            # "message is not modified" is a benign error that occurs when
            # editing a message to the same content or removing buttons that
            # are already removed - log at debug level instead of error
            if resp.status_code == 400 and "message is not modified" in body:
                logger.debug(
                    "telegram.message_not_modified",
                    method=method,
                    url=str(resp.request.url),
                )
            else:
                logger.error(
                    "telegram.http_error",
                    method=method,
                    status=resp.status_code,
                    url=str(resp.request.url),
                    error=str(e),
                    body=body,
                )
            return None

        try:
            payload = resp.json()
        except Exception as e:
            body = resp.text
            logger.error(
                "telegram.bad_response",
                method=method,
                status=resp.status_code,
                url=str(resp.request.url),
                error=str(e),
                error_type=e.__class__.__name__,
                body=body,
            )
            return None

        if not isinstance(payload, dict):
            logger.error(
                "telegram.invalid_payload",
                method=method,
                url=str(resp.request.url),
                payload=payload,
            )
            return None

        if not payload.get("ok"):
            if payload.get("error_code") == 429:
                retry_after = retry_after_from_payload(payload)
                retry_after = 5.0 if retry_after is None else retry_after
                logger.warning(
                    "telegram.rate_limited",
                    method=method,
                    url=str(resp.request.url),
                    retry_after=retry_after,
                )
                raise TelegramRetryAfter(retry_after)
            logger.error(
                "telegram.api_error",
                method=method,
                url=str(resp.request.url),
                payload=payload,
            )
            return None

        logger.debug("telegram.response", method=method, payload=payload)
        return payload.get("result")

    async def get_updates(
        self,
        offset: int | None,
        timeout_s: int = 50,
        allowed_updates: list[str] | None = None,
    ) -> list[dict] | None:
        """Get updates from Telegram (long polling).

        This method retries internally on 429 errors since it's not queued.
        """
        while True:
            try:
                params: dict[str, Any] = {"timeout": timeout_s}
                if offset is not None:
                    params["offset"] = offset
                if allowed_updates is not None:
                    params["allowed_updates"] = allowed_updates
                result = await self._post("getUpdates", params)
                return result if isinstance(result, list) else None
            except TelegramRetryAfter as exc:
                await self._sleep(exc.retry_after)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        disable_notification: bool | None = False,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        message_thread_id: int | None = None,
        reply_markup: dict | None = None,
        *,
        replace_message_id: int | None = None,
    ) -> dict | None:
        """Send a message to a chat.

        Args:
            chat_id: Target chat ID
            text: Message text
            reply_to_message_id: Message to reply to
            disable_notification: Send silently
            entities: Message entities (formatting)
            parse_mode: Parse mode (Markdown, HTML)
            message_thread_id: Forum topic ID
            reply_markup: Inline keyboard markup
            replace_message_id: If provided, supersedes a previous send and
                                deletes the old message after sending
        """

        async def execute() -> dict | None:
            params: dict[str, Any] = {"chat_id": chat_id, "text": text}
            if disable_notification is not None:
                params["disable_notification"] = disable_notification
            if reply_to_message_id is not None:
                params["reply_to_message_id"] = reply_to_message_id
            if entities is not None:
                params["entities"] = entities
            if parse_mode is not None:
                params["parse_mode"] = parse_mode
            if message_thread_id is not None:
                params["message_thread_id"] = message_thread_id
            if reply_markup is not None:
                params["reply_markup"] = reply_markup
            result = await self._post("sendMessage", params)
            return result if isinstance(result, dict) else None

        if replace_message_id is not None:
            await self._outbox.drop_pending(key=("edit", chat_id, replace_message_id))
        result = await self._enqueue_op(
            key=(
                ("send", chat_id, replace_message_id)
                if replace_message_id is not None
                else self._unique_key("send")
            ),
            label="send_message",
            execute=execute,
            priority=SEND_PRIORITY,
            chat_id=chat_id,
        )
        if replace_message_id is not None and result is not None:
            await self.delete_message(chat_id=chat_id, message_id=replace_message_id)
        return result

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
        *,
        wait: bool = True,
    ) -> dict | None:
        """Edit a message's text.

        Args:
            chat_id: Chat containing the message
            message_id: Message to edit
            text: New text
            entities: Message entities (formatting)
            parse_mode: Parse mode (Markdown, HTML)
            reply_markup: Inline keyboard markup
            wait: If False, returns immediately (fire-and-forget)

        Multiple edits to the same message are coalesced - only the latest
        edit is actually sent to Telegram.
        """

        async def execute() -> dict | None:
            params: dict[str, Any] = {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
            }
            if entities is not None:
                params["entities"] = entities
            if parse_mode is not None:
                params["parse_mode"] = parse_mode
            if reply_markup is not None:
                params["reply_markup"] = reply_markup
            result = await self._post("editMessageText", params)
            return result if isinstance(result, dict) else None

        return await self._enqueue_op(
            key=("edit", chat_id, message_id),
            label="edit_message_text",
            execute=execute,
            priority=EDIT_PRIORITY,
            chat_id=chat_id,
            wait=wait,
        )

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
    ) -> bool:
        """Answer a callback query from an inline keyboard button."""

        async def execute() -> bool:
            params: dict[str, Any] = {"callback_query_id": callback_query_id}
            if text is not None:
                params["text"] = text
            result = await self._post("answerCallbackQuery", params)
            return bool(result)

        return bool(
            await self._enqueue_op(
                key=self._unique_key("answer_callback_query"),
                label="answer_callback_query",
                execute=execute,
                priority=SEND_PRIORITY,
                chat_id=None,
            )
        )

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        """Delete a message.

        Automatically drops any pending edits for this message.
        """
        await self.drop_pending_edits(chat_id=chat_id, message_id=message_id)

        async def execute() -> bool:
            result = await self._post(
                "deleteMessage",
                {"chat_id": chat_id, "message_id": message_id},
            )
            return bool(result)

        return bool(
            await self._enqueue_op(
                key=("delete", chat_id, message_id),
                label="delete_message",
                execute=execute,
                priority=DELETE_PRIORITY,
                chat_id=chat_id,
            )
        )

    async def edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: dict | None = None,
    ) -> dict | None:
        """Edit a message's reply markup (inline keyboard).

        Args:
            chat_id: Chat containing the message
            message_id: Message to edit
            reply_markup: New inline keyboard markup (or None/empty to remove)
        """

        async def execute() -> dict | None:
            params: dict[str, Any] = {
                "chat_id": chat_id,
                "message_id": message_id,
            }
            if reply_markup is not None:
                params["reply_markup"] = reply_markup
            result = await self._post("editMessageReplyMarkup", params)
            return result if isinstance(result, dict) else None

        return await self._enqueue_op(
            key=("edit_markup", chat_id, message_id),
            label="edit_message_reply_markup",
            execute=execute,
            priority=EDIT_PRIORITY,
            chat_id=chat_id,
        )

    async def set_my_commands(
        self,
        commands: list[dict[str, Any]],
        *,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> bool:
        """Set the bot's command list."""

        async def execute() -> bool:
            params: dict[str, Any] = {"commands": commands}
            if scope is not None:
                params["scope"] = scope
            if language_code is not None:
                params["language_code"] = language_code
            result = await self._post("setMyCommands", params)
            return bool(result)

        return bool(
            await self._enqueue_op(
                key=self._unique_key("set_my_commands"),
                label="set_my_commands",
                execute=execute,
                priority=SEND_PRIORITY,
                chat_id=None,
            )
        )

    async def get_me(self) -> dict | None:
        """Get information about the bot."""

        async def execute() -> dict | None:
            result = await self._post("getMe", {})
            return result if isinstance(result, dict) else None

        return await self._enqueue_op(
            key=self._unique_key("get_me"),
            label="get_me",
            execute=execute,
            priority=SEND_PRIORITY,
            chat_id=None,
        )

    async def get_chat(self, chat_id: int) -> dict | None:
        """Get information about a chat."""

        async def execute() -> dict | None:
            result = await self._post("getChat", {"chat_id": chat_id})
            return result if isinstance(result, dict) else None

        return await self._enqueue_op(
            key=self._unique_key("get_chat"),
            label="get_chat",
            execute=execute,
            priority=SEND_PRIORITY,
            chat_id=chat_id,
        )

    async def create_forum_topic(
        self,
        chat_id: int,
        name: str,
        icon_color: int | None = None,
        icon_custom_emoji_id: str | None = None,
    ) -> dict | None:
        """Create a forum topic in a supergroup."""

        async def execute() -> dict | None:
            params: dict[str, Any] = {
                "chat_id": chat_id,
                "name": name,
            }
            if icon_color is not None:
                params["icon_color"] = icon_color
            if icon_custom_emoji_id is not None:
                params["icon_custom_emoji_id"] = icon_custom_emoji_id
            result = await self._post("createForumTopic", params)
            return result if isinstance(result, dict) else None

        return await self._enqueue_op(
            key=self._unique_key("create_forum_topic"),
            label="create_forum_topic",
            execute=execute,
            priority=SEND_PRIORITY,
            chat_id=chat_id,
        )

    async def close_forum_topic(self, chat_id: int, message_thread_id: int) -> bool:
        """Close a forum topic."""

        async def execute() -> bool:
            result = await self._post(
                "closeForumTopic",
                {"chat_id": chat_id, "message_thread_id": message_thread_id},
            )
            return bool(result)

        return bool(
            await self._enqueue_op(
                key=self._unique_key("close_forum_topic"),
                label="close_forum_topic",
                execute=execute,
                priority=SEND_PRIORITY,
                chat_id=chat_id,
            )
        )

    async def reopen_forum_topic(self, chat_id: int, message_thread_id: int) -> bool:
        """Reopen a forum topic."""

        async def execute() -> bool:
            result = await self._post(
                "reopenForumTopic",
                {"chat_id": chat_id, "message_thread_id": message_thread_id},
            )
            return bool(result)

        return bool(
            await self._enqueue_op(
                key=self._unique_key("reopen_forum_topic"),
                label="reopen_forum_topic",
                execute=execute,
                priority=SEND_PRIORITY,
                chat_id=chat_id,
            )
        )

    async def delete_forum_topic(self, chat_id: int, message_thread_id: int) -> bool:
        """Delete a forum topic."""

        async def execute() -> bool:
            result = await self._post(
                "deleteForumTopic",
                {"chat_id": chat_id, "message_thread_id": message_thread_id},
            )
            return bool(result)

        return bool(
            await self._enqueue_op(
                key=self._unique_key("delete_forum_topic"),
                label="delete_forum_topic",
                execute=execute,
                priority=SEND_PRIORITY,
                chat_id=chat_id,
            )
        )
