"""Tests for pochi.onboarding module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pochi.onboarding import (
    validate_bot_token,
    validate_chat_access,
    show_available_engines,
)
from pochi.config_store import WORKSPACE_CONFIG_DIR, WORKSPACE_CONFIG_FILE


class TestValidateBotToken:
    """Tests for validate_bot_token function."""

    @pytest.mark.anyio
    async def test_returns_bot_info_on_success(self) -> None:
        """Test returns bot info when token is valid."""
        mock_bot = AsyncMock()
        mock_bot.get_me.return_value = {"id": 123, "username": "test_bot"}

        with patch("pochi.onboarding.TelegramClient", return_value=mock_bot):
            result = await validate_bot_token("valid-token")

        assert result is not None
        assert result["username"] == "test_bot"
        mock_bot.close.assert_called_once()

    @pytest.mark.anyio
    async def test_returns_none_on_error(self) -> None:
        """Test returns None when token is invalid."""
        mock_bot = AsyncMock()
        mock_bot.get_me.side_effect = Exception("Invalid token")

        with patch("pochi.onboarding.TelegramClient", return_value=mock_bot):
            result = await validate_bot_token("invalid-token")

        assert result is None
        mock_bot.close.assert_called_once()


class TestValidateChatAccess:
    """Tests for validate_chat_access function."""

    @pytest.mark.anyio
    async def test_returns_chat_info_on_success(self) -> None:
        """Test returns chat info when bot can access the chat."""
        mock_bot = AsyncMock()
        mock_bot.get_chat.return_value = {"id": -100123, "title": "Test Group"}

        with patch("pochi.onboarding.TelegramClient", return_value=mock_bot):
            result = await validate_chat_access("token", -100123)

        assert result is not None
        assert result["title"] == "Test Group"
        mock_bot.close.assert_called_once()

    @pytest.mark.anyio
    async def test_returns_none_on_error(self) -> None:
        """Test returns None when bot cannot access the chat."""
        mock_bot = AsyncMock()
        mock_bot.get_chat.side_effect = Exception("Chat not found")

        with patch("pochi.onboarding.TelegramClient", return_value=mock_bot):
            result = await validate_chat_access("token", -100999)

        assert result is None
        mock_bot.close.assert_called_once()


class TestShowAvailableEngines:
    """Tests for show_available_engines function."""

    def test_displays_table_without_error(self) -> None:
        """Test that show_available_engines runs without error."""
        # This is a smoke test - just verify it doesn't crash
        with patch("pochi.onboarding.console.print"):
            show_available_engines()


class TestOnboardingHelpers:
    """Tests for onboarding helper functions."""

    def test_config_path_construction(self, tmp_path: Path) -> None:
        """Test that config path is constructed correctly."""
        from pochi.config_store import get_config_path

        expected = tmp_path / WORKSPACE_CONFIG_DIR / WORKSPACE_CONFIG_FILE
        assert get_config_path(tmp_path) == expected
