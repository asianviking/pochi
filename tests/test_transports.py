"""Tests for pochi.transports module."""

from __future__ import annotations

from pathlib import Path

import pytest

from pochi.transports import (
    TransportBackend,
    SetupResult,
    get_transport,
    list_transports,
    check_transport_setup,
    get_default_transport,
)
from pochi.transports.telegram import TelegramTransportBackend, TRANSPORT
from pochi.config import WorkspaceConfig, TelegramConfig
from pochi.settings import ConfigError


class TestSetupResult:
    """Tests for SetupResult dataclass."""

    def test_ready_result(self) -> None:
        """Test creating a ready result."""
        result = SetupResult(ready=True, message="All good")
        assert result.ready is True
        assert result.message == "All good"
        assert result.details is None

    def test_not_ready_result(self) -> None:
        """Test creating a not-ready result."""
        result = SetupResult(
            ready=False,
            message="Missing config",
            details={"missing": "bot_token"},
        )
        assert result.ready is False
        assert result.message == "Missing config"
        assert result.details == {"missing": "bot_token"}


class TestTelegramTransportBackend:
    """Tests for TelegramTransportBackend."""

    def test_has_correct_id(self) -> None:
        """Test that Telegram backend has correct ID."""
        backend = TelegramTransportBackend()
        assert backend.id == "telegram"

    def test_has_description(self) -> None:
        """Test that Telegram backend has description."""
        backend = TelegramTransportBackend()
        assert backend.description
        assert "Telegram" in backend.description

    def test_get_config_section(self) -> None:
        """Test get_config_section returns correct value."""
        backend = TelegramTransportBackend()
        assert backend.get_config_section() == "telegram"

    def test_check_setup_with_telegram_config(self, tmp_path: Path) -> None:
        """Test check_setup passes with proper telegram config."""
        telegram = TelegramConfig(bot_token="token", chat_id=123456)
        config = WorkspaceConfig(
            name="test",
            root=tmp_path,
            telegram=telegram,
            telegram_group_id=123456,
            bot_token="token",
        )

        backend = TelegramTransportBackend()
        result = backend.check_setup(config)

        assert result.ready is True
        assert result.details is not None
        assert result.details["chat_id"] == 123456

    def test_check_setup_with_legacy_config(self, tmp_path: Path) -> None:
        """Test check_setup passes with legacy config fields."""
        config = WorkspaceConfig(
            name="test",
            root=tmp_path,
            telegram_group_id=123456,
            bot_token="legacy-token",
        )

        backend = TelegramTransportBackend()
        result = backend.check_setup(config)

        assert result.ready is True

    def test_check_setup_missing_bot_token(self, tmp_path: Path) -> None:
        """Test check_setup fails when bot_token is missing."""
        config = WorkspaceConfig(
            name="test",
            root=tmp_path,
            telegram_group_id=123456,
            bot_token="",
        )

        backend = TelegramTransportBackend()
        result = backend.check_setup(config)

        assert result.ready is False
        assert "bot_token" in result.message

    def test_check_setup_missing_group_id(self, tmp_path: Path) -> None:
        """Test check_setup fails when group_id is missing."""
        config = WorkspaceConfig(
            name="test",
            root=tmp_path,
            telegram_group_id=0,
            bot_token="token",
        )

        backend = TelegramTransportBackend()
        result = backend.check_setup(config)

        assert result.ready is False
        assert "telegram_group_id" in result.message


class TestTransportRegistry:
    """Tests for transport registry functions."""

    def test_get_transport_telegram(self) -> None:
        """Test getting the telegram transport."""
        transport = get_transport("telegram")
        assert transport is not None
        assert transport.id == "telegram"

    def test_get_transport_unknown_raises(self) -> None:
        """Test getting unknown transport raises ConfigError."""
        with pytest.raises(ConfigError) as exc_info:
            get_transport("unknown_transport")

        assert "unknown_transport" in str(exc_info.value)

    def test_list_transports_includes_telegram(self) -> None:
        """Test that list_transports includes telegram."""
        transports = list_transports()
        assert "telegram" in transports

    def test_get_default_transport(self) -> None:
        """Test get_default_transport returns telegram."""
        default = get_default_transport()
        assert default == "telegram"


class TestCheckTransportSetup:
    """Tests for check_transport_setup function."""

    def test_returns_result_for_valid_transport(self, tmp_path: Path) -> None:
        """Test returns SetupResult for valid transport."""
        config = WorkspaceConfig(
            name="test",
            root=tmp_path,
            telegram_group_id=123456,
            bot_token="token",
        )

        result = check_transport_setup("telegram", config)

        assert isinstance(result, SetupResult)
        assert result.ready is True

    def test_returns_not_ready_for_unknown_transport(self, tmp_path: Path) -> None:
        """Test returns not-ready for unknown transport."""
        config = WorkspaceConfig(
            name="test",
            root=tmp_path,
            telegram_group_id=123456,
            bot_token="token",
        )

        result = check_transport_setup("unknown", config)

        assert result.ready is False
        assert "Unknown transport" in result.message


class TestTransportModuleExport:
    """Tests for transport module exports."""

    def test_telegram_exports_transport(self) -> None:
        """Test telegram module exports TRANSPORT."""
        assert TRANSPORT is not None
        assert isinstance(TRANSPORT, TransportBackend)
        assert TRANSPORT.id == "telegram"
