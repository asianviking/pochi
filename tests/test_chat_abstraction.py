"""Tests for platform-agnostic chat abstraction layer."""

from __future__ import annotations

import pytest

from pochi.chat import (
    ChatUpdate,
    Destination,
    MessageRef,
    destination_for_discord,
    destination_for_telegram,
    message_ref_to_edit_key,
)


class TestMessageRef:
    """Tests for MessageRef dataclass."""

    def test_creates_telegram_ref(self) -> None:
        """Test creating a Telegram message reference."""
        ref = MessageRef(
            platform="telegram",
            channel_id=-123456,
            message_id=789,
        )
        assert ref.platform == "telegram"
        assert ref.channel_id == -123456
        assert ref.message_id == 789
        assert ref.thread_id is None

    def test_creates_discord_ref_with_thread(self) -> None:
        """Test creating a Discord message reference with thread."""
        ref = MessageRef(
            platform="discord",
            channel_id=111111,
            message_id=222222,
            thread_id=333333,
        )
        assert ref.platform == "discord"
        assert ref.channel_id == 111111
        assert ref.message_id == 222222
        assert ref.thread_id == 333333

    def test_is_frozen(self) -> None:
        """Test that MessageRef is immutable."""
        ref = MessageRef(
            platform="telegram",
            channel_id=123,
            message_id=456,
        )
        with pytest.raises(AttributeError):
            ref.message_id = 999  # type: ignore


class TestDestination:
    """Tests for Destination dataclass."""

    def test_creates_basic_destination(self) -> None:
        """Test creating a basic destination."""
        dest = Destination(channel_id=123)
        assert dest.channel_id == 123
        assert dest.thread_id is None
        assert dest.reply_to is None

    def test_creates_destination_with_thread_and_reply(self) -> None:
        """Test creating a destination with thread and reply."""
        dest = Destination(
            channel_id=111,
            thread_id=222,
            reply_to=333,
        )
        assert dest.channel_id == 111
        assert dest.thread_id == 222
        assert dest.reply_to == 333


class TestChatUpdate:
    """Tests for ChatUpdate dataclass."""

    def test_creates_message_update(self) -> None:
        """Test creating a message update."""
        update = ChatUpdate(
            platform="telegram",
            update_type="message",
            channel_id=-123456,
            thread_id=100,
            message_id=789,
            text="Hello world",
            user_id=555,
        )
        assert update.platform == "telegram"
        assert update.update_type == "message"
        assert update.channel_id == -123456
        assert update.thread_id == 100
        assert update.message_id == 789
        assert update.text == "Hello world"
        assert update.user_id == 555

    def test_creates_callback_query_update(self) -> None:
        """Test creating a callback query update."""
        update = ChatUpdate(
            platform="telegram",
            update_type="callback_query",
            callback_query_id="query123",
            callback_data="action:cancel",
            callback_message_id=456,
            callback_chat_id=-789,
        )
        assert update.update_type == "callback_query"
        assert update.callback_query_id == "query123"
        assert update.callback_data == "action:cancel"
        assert update.callback_message_id == 456
        assert update.callback_chat_id == -789

    def test_default_raw_dict(self) -> None:
        """Test that raw defaults to empty dict."""
        update = ChatUpdate(
            platform="discord",
            update_type="message",
        )
        assert update.raw == {}


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_message_ref_to_edit_key(self) -> None:
        """Test creating edit key from MessageRef."""
        ref = MessageRef(
            platform="telegram",
            channel_id=123,
            message_id=456,
        )
        key = message_ref_to_edit_key(ref)
        assert key == ("telegram", 123, 456)

    def test_destination_for_telegram(self) -> None:
        """Test creating Telegram destination."""
        dest = destination_for_telegram(
            group_id=-123456,
            topic_id=100,
            reply_to=789,
        )
        assert dest.channel_id == -123456
        assert dest.thread_id == 100
        assert dest.reply_to == 789

    def test_destination_for_telegram_no_topic(self) -> None:
        """Test creating Telegram destination without topic (General)."""
        dest = destination_for_telegram(
            group_id=-123456,
            topic_id=None,
        )
        assert dest.channel_id == -123456
        assert dest.thread_id is None

    def test_destination_for_discord(self) -> None:
        """Test creating Discord destination."""
        dest = destination_for_discord(
            channel_id=111111,
            thread_id=222222,
            reply_to=333333,
        )
        assert dest.channel_id == 111111
        assert dest.thread_id == 222222
        assert dest.reply_to == 333333

    def test_destination_for_discord_no_thread(self) -> None:
        """Test creating Discord destination without thread."""
        dest = destination_for_discord(channel_id=111111)
        assert dest.channel_id == 111111
        assert dest.thread_id is None
        assert dest.reply_to is None
