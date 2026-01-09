"""Tests for the TopicDebouncer message batching logic."""

from __future__ import annotations

from typing import Any

import anyio
import pytest

from pochi.workspace.bridge import (
    MessageBatch,
    PendingMessage,
    TopicDebouncer,
    _run_debounce_timer,
)


def _make_msg(
    message_id: int,
    text: str,
    *,
    message_thread_id: int | None = None,
    reply_to_message: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a minimal Telegram message dict for testing."""
    msg: dict[str, Any] = {
        "message_id": message_id,
        "text": text,
        "chat": {"id": 123},
    }
    if message_thread_id is not None:
        msg["message_thread_id"] = message_thread_id
    if reply_to_message is not None:
        msg["reply_to_message"] = reply_to_message
    return msg


class TestTopicDebouncerSync:
    """Tests for synchronous TopicDebouncer methods."""

    def test_slash_command_bypasses_debounce(self) -> None:
        """Slash commands should be returned immediately without debouncing."""
        debouncer = TopicDebouncer(window_ms=200.0)
        msg = _make_msg(1, "/help")

        batches = debouncer.add_message(None, msg)

        assert len(batches) == 1
        batch = batches[0]
        assert batch.combined_text == "/help"
        assert batch.message_ids == [1]
        assert batch.last_message_id == 1
        assert batch.topic_id is None
        assert not debouncer.has_pending()

    def test_slash_command_flushes_pending(self) -> None:
        """Slash command should flush any pending messages for the same topic."""
        debouncer = TopicDebouncer(window_ms=200.0)

        # Add a regular message
        batches1 = debouncer.add_message(None, _make_msg(1, "hello"))
        assert batches1 == []
        assert debouncer.has_pending()

        # Add slash command - should flush pending first
        batches2 = debouncer.add_message(None, _make_msg(2, "/help"))
        assert len(batches2) == 2

        # First batch is the flushed pending message
        assert batches2[0].combined_text == "hello"
        assert batches2[0].message_ids == [1]

        # Second batch is the slash command
        assert batches2[1].combined_text == "/help"
        assert batches2[1].message_ids == [2]

        assert not debouncer.has_pending()

    def test_regular_message_queued(self) -> None:
        """Regular messages should be queued without immediate batching."""
        debouncer = TopicDebouncer(window_ms=200.0)
        msg = _make_msg(1, "hello")

        batches = debouncer.add_message(None, msg)

        assert batches == []
        assert debouncer.has_pending()

    def test_multiple_messages_same_topic(self) -> None:
        """Multiple messages to same topic should be queued together."""
        debouncer = TopicDebouncer(window_ms=200.0)

        batches1 = debouncer.add_message(
            123, _make_msg(1, "hello", message_thread_id=123)
        )
        batches2 = debouncer.add_message(
            123, _make_msg(2, "world", message_thread_id=123)
        )

        assert batches1 == []
        assert batches2 == []
        assert debouncer.has_pending()

        # Flush to check combined content
        flushed = debouncer.flush_all()
        assert len(flushed) == 1
        assert flushed[0].combined_text == "hello\nworld"
        assert flushed[0].message_ids == [1, 2]
        assert flushed[0].last_message_id == 2
        assert flushed[0].topic_id == 123

    def test_messages_different_topics(self) -> None:
        """Messages to different topics should be in separate pending queues."""
        debouncer = TopicDebouncer(window_ms=200.0)

        debouncer.add_message(123, _make_msg(1, "topic 123", message_thread_id=123))
        debouncer.add_message(456, _make_msg(2, "topic 456", message_thread_id=456))
        debouncer.add_message(None, _make_msg(3, "general"))

        flushed = debouncer.flush_all()
        assert len(flushed) == 3

        # Sort by topic_id for deterministic testing
        texts = {b.topic_id: b.combined_text for b in flushed}
        assert texts[123] == "topic 123"
        assert texts[456] == "topic 456"
        assert texts[None] == "general"

    def test_first_reply_preserved(self) -> None:
        """First message's reply_to should be preserved in batch."""
        debouncer = TopicDebouncer(window_ms=200.0)
        reply = {"message_id": 100, "text": "previous message"}

        debouncer.add_message(None, _make_msg(1, "first", reply_to_message=reply))
        debouncer.add_message(None, _make_msg(2, "second"))

        flushed = debouncer.flush_all()
        assert len(flushed) == 1
        assert flushed[0].first_reply_to == reply

    def test_flush_all_clears_state(self) -> None:
        """flush_all should clear all pending state."""
        debouncer = TopicDebouncer(window_ms=200.0)

        debouncer.add_message(123, _make_msg(1, "hello", message_thread_id=123))
        debouncer.add_message(456, _make_msg(2, "world", message_thread_id=456))

        assert debouncer.has_pending()
        debouncer.flush_all()
        assert not debouncer.has_pending()

    def test_check_expired_with_fake_clock(self) -> None:
        """check_expired should return batches whose window has expired."""
        clock_value = [0.0]

        def fake_clock() -> float:
            return clock_value[0]

        debouncer = TopicDebouncer(window_ms=200.0, clock=fake_clock)

        # Add message at t=0
        clock_value[0] = 0.0
        debouncer.add_message(None, _make_msg(1, "hello"))

        # Check at t=100ms - not expired yet
        clock_value[0] = 0.1
        batches = debouncer.check_expired()
        assert batches == []
        assert debouncer.has_pending()

        # Check at t=200ms - should be expired
        clock_value[0] = 0.2
        batches = debouncer.check_expired()
        assert len(batches) == 1
        assert batches[0].combined_text == "hello"
        assert not debouncer.has_pending()

    def test_new_message_resets_deadline(self) -> None:
        """Adding a new message should reset the debounce deadline."""
        clock_value = [0.0]

        def fake_clock() -> float:
            return clock_value[0]

        debouncer = TopicDebouncer(window_ms=200.0, clock=fake_clock)

        # Add first message at t=0
        clock_value[0] = 0.0
        debouncer.add_message(None, _make_msg(1, "first"))

        # Add second message at t=150ms
        clock_value[0] = 0.15
        debouncer.add_message(None, _make_msg(2, "second"))

        # Check at t=200ms - should NOT be expired (deadline reset to t=350ms)
        clock_value[0] = 0.2
        batches = debouncer.check_expired()
        assert batches == []
        assert debouncer.has_pending()

        # Check at t=350ms - should be expired
        clock_value[0] = 0.35
        batches = debouncer.check_expired()
        assert len(batches) == 1
        assert batches[0].combined_text == "first\nsecond"

    def test_next_deadline(self) -> None:
        """next_deadline should return the soonest deadline."""
        clock_value = [0.0]

        def fake_clock() -> float:
            return clock_value[0]

        debouncer = TopicDebouncer(window_ms=200.0, clock=fake_clock)

        # No pending - no deadline
        assert debouncer.next_deadline() is None

        # Add message at t=0
        clock_value[0] = 0.0
        debouncer.add_message(123, _make_msg(1, "hello", message_thread_id=123))
        assert debouncer.next_deadline() == 0.2

        # Add message to another topic at t=100ms
        clock_value[0] = 0.1
        debouncer.add_message(456, _make_msg(2, "world", message_thread_id=456))

        # Earliest deadline is still 0.2 (topic 123)
        assert debouncer.next_deadline() == 0.2

    def test_raw_messages_preserved(self) -> None:
        """raw_messages should contain the original message dicts."""
        debouncer = TopicDebouncer(window_ms=200.0)

        msg1 = _make_msg(1, "first")
        msg2 = _make_msg(2, "second")

        debouncer.add_message(None, msg1)
        debouncer.add_message(None, msg2)

        flushed = debouncer.flush_all()
        assert len(flushed) == 1
        assert flushed[0].raw_messages == [msg1, msg2]


class TestTopicDebouncerAsync:
    """Async tests for debouncer timer behavior."""

    @pytest.mark.anyio
    async def test_timer_fires_batch(self) -> None:
        """Timer should fire batches after window expires."""
        debouncer = TopicDebouncer(window_ms=50.0)
        received_batches: list[MessageBatch] = []

        async def on_batch(batch: MessageBatch) -> None:
            received_batches.append(batch)

        async with anyio.create_task_group() as tg:
            tg.start_soon(_run_debounce_timer, debouncer, on_batch)

            # Add a message
            debouncer.add_message(None, _make_msg(1, "hello"))

            # Wait for timer to fire (50ms + some margin)
            await anyio.sleep(0.1)

            # Should have received the batch
            assert len(received_batches) == 1
            assert received_batches[0].combined_text == "hello"

            tg.cancel_scope.cancel()

    @pytest.mark.anyio
    async def test_timer_waits_for_messages(self) -> None:
        """Timer should wait when no pending messages."""
        debouncer = TopicDebouncer(window_ms=50.0)
        received_batches: list[MessageBatch] = []

        async def on_batch(batch: MessageBatch) -> None:
            received_batches.append(batch)

        async with anyio.create_task_group() as tg:
            tg.start_soon(_run_debounce_timer, debouncer, on_batch)

            # Wait without adding messages
            await anyio.sleep(0.1)
            assert received_batches == []

            # Now add a message
            debouncer.add_message(None, _make_msg(1, "hello"))

            # Wait for timer to fire
            await anyio.sleep(0.1)
            assert len(received_batches) == 1

            tg.cancel_scope.cancel()

    @pytest.mark.anyio
    async def test_batching_rapid_messages(self) -> None:
        """Rapid messages should be batched together."""
        debouncer = TopicDebouncer(window_ms=100.0)
        received_batches: list[MessageBatch] = []

        async def on_batch(batch: MessageBatch) -> None:
            received_batches.append(batch)

        async with anyio.create_task_group() as tg:
            tg.start_soon(_run_debounce_timer, debouncer, on_batch)

            # Add messages rapidly (within the 100ms window)
            debouncer.add_message(None, _make_msg(1, "hello"))
            await anyio.sleep(0.02)  # 20ms
            debouncer.add_message(None, _make_msg(2, "world"))
            await anyio.sleep(0.02)  # 40ms total
            debouncer.add_message(None, _make_msg(3, "!"))

            # Wait for batch - deadline should be ~100ms after last message
            await anyio.sleep(0.15)

            # Should have received a single batch with all three messages
            assert len(received_batches) == 1
            assert received_batches[0].combined_text == "hello\nworld\n!"
            assert received_batches[0].message_ids == [1, 2, 3]

            tg.cancel_scope.cancel()

    @pytest.mark.anyio
    async def test_separate_batches_for_slow_messages(self) -> None:
        """Messages sent after window expires should be in separate batches."""
        debouncer = TopicDebouncer(window_ms=50.0)
        received_batches: list[MessageBatch] = []

        async def on_batch(batch: MessageBatch) -> None:
            received_batches.append(batch)

        async with anyio.create_task_group() as tg:
            tg.start_soon(_run_debounce_timer, debouncer, on_batch)

            # First message
            debouncer.add_message(None, _make_msg(1, "first"))

            # Wait for it to be batched
            await anyio.sleep(0.1)
            assert len(received_batches) == 1
            assert received_batches[0].combined_text == "first"

            # Second message (after window expired)
            debouncer.add_message(None, _make_msg(2, "second"))

            # Wait for second batch
            await anyio.sleep(0.1)
            assert len(received_batches) == 2
            assert received_batches[1].combined_text == "second"

            tg.cancel_scope.cancel()

    @pytest.mark.anyio
    async def test_independent_topic_batching(self) -> None:
        """Different topics should batch independently."""
        debouncer = TopicDebouncer(window_ms=50.0)
        received_batches: list[MessageBatch] = []

        async def on_batch(batch: MessageBatch) -> None:
            received_batches.append(batch)

        async with anyio.create_task_group() as tg:
            tg.start_soon(_run_debounce_timer, debouncer, on_batch)

            # Add messages to different topics
            debouncer.add_message(123, _make_msg(1, "topic 123", message_thread_id=123))
            debouncer.add_message(456, _make_msg(2, "topic 456", message_thread_id=456))

            # Wait for both to be batched
            await anyio.sleep(0.1)

            assert len(received_batches) == 2
            topic_texts = {b.topic_id: b.combined_text for b in received_batches}
            assert topic_texts[123] == "topic 123"
            assert topic_texts[456] == "topic 456"

            tg.cancel_scope.cancel()


class TestPendingMessageDataclass:
    """Tests for PendingMessage dataclass."""

    def test_fields(self) -> None:
        """Verify PendingMessage fields."""
        raw = _make_msg(1, "hello")
        pending = PendingMessage(
            message_id=1,
            text="hello",
            timestamp=1234.5,
            reply_to=None,
            raw_message=raw,
        )
        assert pending.message_id == 1
        assert pending.text == "hello"
        assert pending.timestamp == 1234.5
        assert pending.reply_to is None
        assert pending.raw_message == raw


class TestMessageBatchDataclass:
    """Tests for MessageBatch dataclass."""

    def test_fields(self) -> None:
        """Verify MessageBatch fields."""
        raw = [_make_msg(1, "hello")]
        batch = MessageBatch(
            message_ids=[1],
            combined_text="hello",
            first_reply_to=None,
            last_message_id=1,
            topic_id=123,
            raw_messages=raw,
        )
        assert batch.message_ids == [1]
        assert batch.combined_text == "hello"
        assert batch.first_reply_to is None
        assert batch.last_message_id == 1
        assert batch.topic_id == 123
        assert batch.raw_messages == raw

    def test_default_raw_messages(self) -> None:
        """raw_messages should default to empty list."""
        batch = MessageBatch(
            message_ids=[1],
            combined_text="hello",
            first_reply_to=None,
            last_message_id=1,
            topic_id=None,
        )
        assert batch.raw_messages == []
