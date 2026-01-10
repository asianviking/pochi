"""Tests for pochi.cli module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from pochi import __version__
from pochi.cli import (
    _build_runner_entry,
    _load_raw_config,
    _version_callback,
    app,
)
from pochi.backends import EngineBackend
from pochi.workspace.config import (
    WORKSPACE_CONFIG_DIR,
    WORKSPACE_CONFIG_FILE,
    create_workspace,
)


runner = CliRunner()


class TestVersionCallback:
    """Tests for _version_callback function."""

    def test_exits_when_true(self) -> None:
        """Test version callback exits when value is True."""
        import typer

        with pytest.raises(typer.Exit):
            _version_callback(True)

    def test_does_nothing_when_false(self) -> None:
        """Test version callback does nothing when value is False."""
        # Should not raise
        _version_callback(False)


class TestLoadRawConfig:
    """Tests for _load_raw_config function."""

    def test_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        """Test returns empty dict when file doesn't exist."""
        config_path = tmp_path / "nonexistent.toml"
        result = _load_raw_config(config_path)
        assert result == {}

    def test_loads_valid_toml(self, tmp_path: Path) -> None:
        """Test loads valid TOML file."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[workspace]\nname = "test"\n')
        result = _load_raw_config(config_path)
        assert result["workspace"]["name"] == "test"


class TestBuildRunnerEntry:
    """Tests for _build_runner_entry function."""

    def test_returns_unavailable_when_cli_not_found(self, tmp_path: Path) -> None:
        """Test returns unavailable entry when CLI not found."""
        backend = EngineBackend(
            id="nonexistent",
            build_runner=lambda cfg, path: MagicMock(),
            cli_cmd="nonexistent-cli-tool-xyz",
        )
        entry = _build_runner_entry(backend, {}, tmp_path / "config.toml")
        assert entry.available is False
        assert "not found" in (entry.issue or "")

    def test_returns_unavailable_on_build_error(self, tmp_path: Path) -> None:
        """Test returns unavailable entry when build_runner fails."""

        def failing_builder(cfg, path):
            raise ValueError("Build failed")

        backend = EngineBackend(
            id="claude",
            build_runner=failing_builder,
            cli_cmd="python",  # Use python as it's always available
        )
        entry = _build_runner_entry(backend, {}, tmp_path / "config.toml")
        assert entry.available is False
        assert "Build failed" in (entry.issue or "")


class TestCLICommands:
    """Tests for CLI commands."""

    def test_version_flag(self) -> None:
        """Test --version flag shows version."""
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.stdout

    def test_info_command_no_workspace(self, tmp_path: Path, monkeypatch) -> None:
        """Test info command when not in workspace."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["info"])
        assert result.exit_code == 1
        assert "not in a workspace" in result.output

    def test_info_command_with_workspace(self, tmp_path: Path, monkeypatch) -> None:
        """Test info command with valid workspace."""
        monkeypatch.chdir(tmp_path)
        create_workspace(
            root=tmp_path,
            name="test-workspace",
            telegram_group_id=123,
            bot_token="test-token",
        )
        result = runner.invoke(app, ["info"])
        assert result.exit_code == 0
        assert "test-workspace" in result.stdout

    def test_init_command_creates_workspace(self, tmp_path: Path, monkeypatch) -> None:
        """Test init command creates workspace."""
        monkeypatch.chdir(tmp_path)

        # Mock the bot token validation
        mock_bot_info = {"username": "test_bot"}
        mock_chat_info = {"title": "Test Group"}

        with patch(
            "pochi.cli._validate_bot_token", new_callable=AsyncMock
        ) as mock_validate_token:
            with patch(
                "pochi.cli._validate_group_access", new_callable=AsyncMock
            ) as mock_validate_group:
                mock_validate_token.return_value = mock_bot_info
                mock_validate_group.return_value = mock_chat_info

                result = runner.invoke(
                    app,
                    ["init", "--bot-token", "test-token", "--group-id", "123"],
                )

        assert result.exit_code == 0
        assert (
            "Initialized workspace" in result.stdout
            or "Created workspace" in result.stdout
        )

        # Verify workspace was created
        config_path = tmp_path / WORKSPACE_CONFIG_DIR / WORKSPACE_CONFIG_FILE
        assert config_path.exists()

    def test_init_command_fails_invalid_group_id(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Test init command fails with invalid group ID."""
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(
            app,
            ["init", "--bot-token", "test-token"],
            input="not-a-number\n",
        )
        assert result.exit_code == 1
        assert "integer" in result.output

    def test_init_command_fails_when_workspace_exists(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Test init command fails when workspace already exists."""
        monkeypatch.chdir(tmp_path)
        create_workspace(
            root=tmp_path,
            name="existing",
            telegram_group_id=123,
            bot_token="token",
        )

        result = runner.invoke(
            app,
            ["init", "--bot-token", "new-token", "--group-id", "456"],
        )
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_init_in_subfolder(self, tmp_path: Path, monkeypatch) -> None:
        """Test init command creates workspace in subfolder."""
        monkeypatch.chdir(tmp_path)

        mock_bot_info = {"username": "test_bot"}
        mock_chat_info = {"title": "Test Group"}

        with patch(
            "pochi.cli._validate_bot_token", new_callable=AsyncMock
        ) as mock_validate_token:
            with patch(
                "pochi.cli._validate_group_access", new_callable=AsyncMock
            ) as mock_validate_group:
                mock_validate_token.return_value = mock_bot_info
                mock_validate_group.return_value = mock_chat_info

                result = runner.invoke(
                    app,
                    [
                        "init",
                        "my-workspace",
                        "--bot-token",
                        "test-token",
                        "--group-id",
                        "123",
                    ],
                )

        assert result.exit_code == 0

        # Verify workspace was created in subfolder
        config_path = (
            tmp_path / "my-workspace" / WORKSPACE_CONFIG_DIR / WORKSPACE_CONFIG_FILE
        )
        assert config_path.exists()

    def test_init_fails_invalid_bot_token(self, tmp_path: Path, monkeypatch) -> None:
        """Test init command fails with invalid bot token."""
        monkeypatch.chdir(tmp_path)

        with patch(
            "pochi.cli._validate_bot_token", new_callable=AsyncMock
        ) as mock_validate:
            mock_validate.return_value = None  # Invalid token

            result = runner.invoke(
                app,
                ["init", "--bot-token", "invalid-token", "--group-id", "123"],
            )

        assert result.exit_code == 1
        assert "invalid bot token" in result.output

    def test_init_fails_cannot_access_group(self, tmp_path: Path, monkeypatch) -> None:
        """Test init command fails when bot cannot access group."""
        monkeypatch.chdir(tmp_path)

        mock_bot_info = {"username": "test_bot"}

        with patch(
            "pochi.cli._validate_bot_token", new_callable=AsyncMock
        ) as mock_validate_token:
            with patch(
                "pochi.cli._validate_group_access", new_callable=AsyncMock
            ) as mock_validate_group:
                mock_validate_token.return_value = mock_bot_info
                mock_validate_group.return_value = None  # Cannot access group

                result = runner.invoke(
                    app,
                    ["init", "--bot-token", "test-token", "--group-id", "123"],
                )

        assert result.exit_code == 1
        assert "cannot access group" in result.output


class TestDefaultRunCommand:
    """Tests for default run command."""

    def test_run_fails_without_workspace(self, tmp_path: Path, monkeypatch) -> None:
        """Test run fails when not in workspace."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, [])
        assert result.exit_code == 1
        assert "not in a workspace" in result.output

    def test_run_fails_without_transport_config(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Test run fails when no transport is configured."""
        monkeypatch.chdir(tmp_path)
        # Create workspace without any transport config
        config_dir = tmp_path / WORKSPACE_CONFIG_DIR
        config_dir.mkdir()
        (config_dir / WORKSPACE_CONFIG_FILE).write_text('[workspace]\nname = "test"\n')

        result = runner.invoke(app, [])
        assert result.exit_code == 1
        assert "no transports configured" in result.output

    def test_run_fails_without_bot_token(self, tmp_path: Path, monkeypatch) -> None:
        """Test run fails when bot_token is missing."""
        monkeypatch.chdir(tmp_path)
        # Create workspace with transport but missing bot_token
        config_dir = tmp_path / WORKSPACE_CONFIG_DIR
        config_dir.mkdir()
        (config_dir / WORKSPACE_CONFIG_FILE).write_text(
            '[workspace]\nname = "test"\n'
            "[transports.telegram]\n"
            'bot_token = ""\n'
            "chat_id = 123\n"
        )

        result = runner.invoke(app, [])
        assert result.exit_code == 1
        # Either bot_token error, transport error, or no engines available (in CI)
        assert (
            "bot_token" in result.output.lower()
            or "transport" in result.output
            or "no engines available" in result.output.lower()
        )

    def test_run_fails_without_chat_id(self, tmp_path: Path, monkeypatch) -> None:
        """Test run fails when chat_id is missing."""
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / WORKSPACE_CONFIG_DIR
        config_dir.mkdir()
        (config_dir / WORKSPACE_CONFIG_FILE).write_text(
            '[workspace]\nname = "test"\n'
            "[transports.telegram]\n"
            'bot_token = "test"\n'
            "chat_id = 0\n"
        )

        result = runner.invoke(app, [])
        assert result.exit_code == 1
        # Either chat_id error, transport error, or no engines available (in CI)
        assert (
            "chat_id" in result.output.lower()
            or "transport" in result.output
            or "no engines available" in result.output.lower()
        )


class TestInfoCommandDetails:
    """Additional tests for info command."""

    def test_info_shows_workspace_details(self, tmp_path: Path, monkeypatch) -> None:
        """Test info command shows workspace configuration."""
        monkeypatch.chdir(tmp_path)
        create_workspace(
            root=tmp_path,
            name="detailed-workspace",
            telegram_group_id=999888777,
            bot_token="test-token-abc",
        )
        result = runner.invoke(app, ["info"])
        assert result.exit_code == 0
        assert "detailed-workspace" in result.stdout
        # Should show config path or some workspace info
        assert ".pochi" in result.stdout or "workspace" in result.stdout.lower()


class TestInitCommandValidation:
    """Additional tests for init command validation."""

    def test_init_prompts_for_missing_bot_token(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Test init command prompts when bot token missing."""
        monkeypatch.chdir(tmp_path)

        # Simulate empty input
        result = runner.invoke(
            app,
            ["init", "--group-id", "123"],
            input="\n",  # Empty token
        )
        assert result.exit_code == 1

    def test_init_prompts_for_missing_group_id(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Test init command prompts when group id missing."""
        monkeypatch.chdir(tmp_path)

        # With bot token but no group ID - should prompt
        result = runner.invoke(
            app,
            ["init", "--bot-token", "test-token"],
            input="\n",  # Empty group ID
        )
        assert result.exit_code == 1
