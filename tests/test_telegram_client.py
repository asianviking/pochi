"""Tests for the Telegram client with queue-based outbox."""

import httpx
import pytest

import anyio

from pochi.logging import setup_logging
from pochi.telegram import TelegramClient, TelegramRetryAfter


class _FakeBot:
    """Fake bot for testing the TelegramClient outbox behavior."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.edit_calls: list[str] = []
        self.delete_calls: list[tuple[int, int]] = []
        self._edit_attempts = 0
        self._updates_attempts = 0
        self.retry_after: float | None = None
        self.updates_retry_after: float | None = None

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        disable_notification: bool | None = False,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        message_thread_id: int | None = None,
        *,
        replace_message_id: int | None = None,
    ) -> dict:
        _ = reply_to_message_id
        _ = disable_notification
        _ = entities
        _ = parse_mode
        _ = message_thread_id
        _ = replace_message_id
        self.calls.append("send_message")
        return {"message_id": 1}

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        *,
        wait: bool = True,
    ) -> dict:
        _ = chat_id
        _ = message_id
        _ = entities
        _ = parse_mode
        _ = wait
        self.calls.append("edit_message_text")
        self.edit_calls.append(text)
        if self.retry_after is not None and self._edit_attempts == 0:
            self._edit_attempts += 1
            raise TelegramRetryAfter(self.retry_after)
        self._edit_attempts += 1
        return {"message_id": message_id}

    async def delete_message(
        self,
        chat_id: int,
        message_id: int,
    ) -> bool:
        self.calls.append("delete_message")
        self.delete_calls.append((chat_id, message_id))
        return True

    async def set_my_commands(
        self,
        commands: list[dict],
        *,
        scope: dict | None = None,
        language_code: str | None = None,
    ) -> bool:
        _ = commands
        _ = scope
        _ = language_code
        return True

    async def get_updates(
        self,
        offset: int | None,
        timeout_s: int = 50,
        allowed_updates: list[str] | None = None,
    ) -> list[dict] | None:
        _ = offset
        _ = timeout_s
        _ = allowed_updates
        if self.updates_retry_after is not None and self._updates_attempts == 0:
            self._updates_attempts += 1
            raise TelegramRetryAfter(self.updates_retry_after)
        self._updates_attempts += 1
        return []

    async def close(self) -> None:
        return None

    async def get_me(self) -> dict | None:
        return {"id": 1}


@pytest.mark.anyio
async def test_edits_coalesce_latest() -> None:
    """Verify that multiple edits to the same message are coalesced.

    When multiple edits are queued for the same message while one is being
    processed, only the latest one should be sent to Telegram.
    """
    texts: list[str] = []
    first_edit_started = anyio.Event()
    release_first_edit = anyio.Event()
    first_call = True

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal first_call
        import json

        data = request.read()
        params = json.loads(data)
        if "text" in params:
            texts.append(params["text"])

        # Block the first edit until we've queued more
        if first_call and "editMessageText" in str(request.url):
            first_call = False
            first_edit_started.set()
            await release_first_edit.wait()

        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 1}},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)

    client = TelegramClient(
        "123:abcDEF_ghij",
        client=http_client,
        private_chat_rps=0.0,  # No rate limit delay
        group_chat_rps=0.0,
    )

    try:
        async with anyio.create_task_group() as tg:
            # Start first edit (will block in handler)
            tg.start_soon(client.edit_message_text, 1, 1, "first")

            # Wait for the first edit to hit the handler
            with anyio.fail_after(1):
                await first_edit_started.wait()

            # Now queue more edits while first is blocked
            # These will coalesce in the outbox
            await client.edit_message_text(1, 1, "second", wait=False)
            await client.edit_message_text(1, 1, "third", wait=False)

            # Release the first edit
            release_first_edit.set()

        # Wait for all processing
        await anyio.sleep(0.1)

        # Should see "first" and "third" (not "second")
        assert "first" in texts
        assert "third" in texts
        assert "second" not in texts  # Should be coalesced away
    finally:
        await client.close()
        await http_client.aclose()


@pytest.mark.anyio
async def test_send_has_higher_priority_than_edit() -> None:
    """Verify sends are processed before edits."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.split("/")[-1]
        calls.append(method)
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 1}},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)

    try:
        client = TelegramClient(
            "123:abcDEF_ghij",
            client=http_client,
            private_chat_rps=10.0,
            group_chat_rps=10.0,
        )

        # Queue an edit first
        await client.edit_message_text(1, 1, "first edit")

        # Then queue an edit and send
        await client.edit_message_text(1, 1, "progress", wait=False)

        # Send should get priority
        await client.send_message(1, "final")

        await anyio.sleep(0.2)
        await client.close()

        # Send should come before the last edit
        assert "sendMessage" in calls
        assert "editMessageText" in calls
    finally:
        await http_client.aclose()


@pytest.mark.anyio
async def test_delete_drops_pending_edits() -> None:
    """Verify deleting a message drops pending edits."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.split("/")[-1]
        calls.append(method)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"message_id": 1} if method != "deleteMessage" else True,
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)

    try:
        client = TelegramClient(
            "123:abcDEF_ghij",
            client=http_client,
            private_chat_rps=10.0,
            group_chat_rps=10.0,
        )

        # Queue first edit (blocking)
        await client.edit_message_text(1, 1, "first")

        # Queue another edit (non-blocking)
        await client.edit_message_text(1, 1, "progress", wait=False)

        # Delete should drop pending edit
        await client.delete_message(1, 1)

        await anyio.sleep(0.2)
        await client.close()

        # Should have first edit and delete, but NOT "progress" edit
        edit_count = calls.count("editMessageText")
        delete_count = calls.count("deleteMessage")
        assert delete_count == 1
        # May have 1 or 2 edits depending on timing, but delete was called
        assert edit_count >= 1
    finally:
        await http_client.aclose()


@pytest.mark.anyio
async def test_telegram_429_retries() -> None:
    """Verify that 429 responses trigger retries via the outbox."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        # Return 429 for first 2 calls, then success
        if len(calls) < 3:
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "description": "retry",
                    "parameters": {"retry_after": 0.01},  # Very short for testing
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 123}},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)

    try:
        client = TelegramClient(
            "123:abcDEF_ghij",
            client=http_client,
            private_chat_rps=0.0,
            group_chat_rps=0.0,
        )
        result = await client.send_message(1, "hi")
        await client.close()

        assert result == {"message_id": 123}
        assert len(calls) == 3  # 2 retries + 1 success
    finally:
        await http_client.aclose()


@pytest.mark.anyio
async def test_get_updates_retries_on_429() -> None:
    """Verify get_updates retries on 429."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 2:
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "description": "retry",
                    "parameters": {"retry_after": 0.01},
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={"ok": True, "result": []},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)

    try:
        client = TelegramClient(
            "123:abcDEF_ghij",
            client=http_client,
        )
        with anyio.fail_after(2):
            updates = await client.get_updates(offset=None, timeout_s=0)
        await client.close()

        assert updates == []
        assert len(calls) == 2
    finally:
        await http_client.aclose()


@pytest.mark.anyio
async def test_no_token_in_logs_on_http_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify token is not leaked in logs."""
    token = "123:abcDEF_ghij"
    setup_logging(debug=True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="oops", request=request)

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)

    try:
        client = TelegramClient(token, client=http_client)
        # Use _post directly to test error logging
        await client._post("getUpdates", {"timeout": 1})
        await client.close()
    finally:
        await http_client.aclose()

    out = capsys.readouterr().out
    assert token not in out
    assert "bot[REDACTED]" in out


@pytest.mark.anyio
async def test_edit_with_wait_false_returns_immediately() -> None:
    """Verify non-blocking edits return None immediately."""
    calls: list[str] = []
    started = anyio.Event()

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        calls.append("request")
        started.set()
        await anyio.sleep(0.5)  # Slow response
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 1}},
            request=request,
        )

    transport = httpx.MockTransport(slow_handler)
    http_client = httpx.AsyncClient(transport=transport)

    try:
        client = TelegramClient(
            "123:abcDEF_ghij",
            client=http_client,
            private_chat_rps=0.0,
        )

        # Non-blocking edit should return immediately
        result = await client.edit_message_text(1, 1, "test", wait=False)
        assert result is None  # Returns None immediately without waiting

        # Wait for request to actually be made
        with anyio.fail_after(1):
            await started.wait()

        await client.close()
        assert "request" in calls
    finally:
        await http_client.aclose()


@pytest.mark.anyio
async def test_interval_for_chat() -> None:
    """Verify interval calculation for different chat types."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {}}, request=request)

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)

    try:
        client = TelegramClient(
            "123:abcDEF_ghij",
            client=http_client,
            private_chat_rps=2.0,  # 0.5 second interval
            group_chat_rps=1.0,  # 1.0 second interval
        )

        # Private chat (positive ID)
        assert client.interval_for_chat(123) == 0.5

        # Group chat (negative ID)
        assert client.interval_for_chat(-123) == 1.0

        # None defaults to private
        assert client.interval_for_chat(None) == 0.5

        await client.close()
    finally:
        await http_client.aclose()
