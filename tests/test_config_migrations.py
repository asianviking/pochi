"""Tests for pochi.config_migrations module."""

from __future__ import annotations

from pathlib import Path

import tomlkit

from pochi.config_migrations import (
    migrate_config,
    migrate_config_file,
    _migrate_repos_to_folders,
    _migrate_legacy_telegram,
)
from pochi.config_store import (
    WORKSPACE_CONFIG_DIR,
    WORKSPACE_CONFIG_FILE,
    read_raw_toml,
)


def _write_config(data: dict, tmp_path: Path) -> Path:
    """Helper to write config dict to TOML file."""
    config_dir = tmp_path / WORKSPACE_CONFIG_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / WORKSPACE_CONFIG_FILE
    config_path.write_text(tomlkit.dumps(data))
    return config_path


class TestMigrateReposToFolders:
    """Tests for _migrate_repos_to_folders migration."""

    def test_migrates_repos_to_folders(self) -> None:
        """Test moving [repos] to [folders]."""
        config: dict = {
            "workspace": {"name": "test"},
            "repos": {"my-repo": {"path": "my-repo", "topic_id": 123}},
        }

        result = _migrate_repos_to_folders(config)

        assert result is True
        assert "repos" not in config
        assert "folders" in config
        folders = config["folders"]
        assert isinstance(folders, dict)
        assert folders["my-repo"]["topic_id"] == 123

    def test_does_nothing_if_no_repos(self) -> None:
        """Test no change if repos section doesn't exist."""
        config = {"workspace": {"name": "test"}}

        result = _migrate_repos_to_folders(config)

        assert result is False
        assert "folders" not in config

    def test_removes_repos_if_folders_exists(self) -> None:
        """Test removes repos if folders already exists."""
        config = {
            "workspace": {"name": "test"},
            "folders": {"new-folder": {"path": "new-folder"}},
            "repos": {"old-repo": {"path": "old-repo"}},
        }

        result = _migrate_repos_to_folders(config)

        assert result is True
        assert "repos" not in config
        # Folders should be unchanged
        assert config["folders"] == {"new-folder": {"path": "new-folder"}}


class TestMigrateLegacyTelegram:
    """Tests for _migrate_legacy_telegram migration."""

    def test_migrates_bot_token_and_group_id(self) -> None:
        """Test moving bot_token and telegram_group_id to [telegram]."""
        config = {
            "workspace": {
                "name": "test",
                "bot_token": "secret-token",
                "telegram_group_id": 123456,
            },
        }

        result = _migrate_legacy_telegram(config)

        assert result is True
        assert "telegram" in config
        assert config["telegram"]["bot_token"] == "secret-token"
        assert config["telegram"]["chat_id"] == 123456
        # Legacy fields should be removed
        assert "bot_token" not in config["workspace"]
        assert "telegram_group_id" not in config["workspace"]

    def test_does_nothing_if_no_legacy_fields(self) -> None:
        """Test no change if no legacy telegram fields."""
        config = {
            "workspace": {"name": "test"},
        }

        result = _migrate_legacy_telegram(config)

        assert result is False
        assert "telegram" not in config

    def test_does_not_overwrite_existing_telegram(self) -> None:
        """Test doesn't overwrite if [telegram] section already exists."""
        config = {
            "workspace": {
                "name": "test",
                "bot_token": "old-token",
                "telegram_group_id": 111,
            },
            "telegram": {
                "bot_token": "new-token",
                "chat_id": 999,
            },
        }

        result = _migrate_legacy_telegram(config)

        assert result is True
        # Existing telegram values should be preserved
        assert config["telegram"]["bot_token"] == "new-token"
        assert config["telegram"]["chat_id"] == 999
        # Legacy fields should be removed
        assert "bot_token" not in config["workspace"]
        assert "telegram_group_id" not in config["workspace"]

    def test_partial_migration(self) -> None:
        """Test migrates only missing fields."""
        config = {
            "workspace": {
                "name": "test",
                "bot_token": "token-from-workspace",
                "telegram_group_id": 123,
            },
            "telegram": {
                "bot_token": "existing-token",
                # chat_id is missing
            },
        }

        result = _migrate_legacy_telegram(config)

        assert result is True
        # Existing token should be preserved
        assert config["telegram"]["bot_token"] == "existing-token"
        # Missing chat_id should be migrated from telegram_group_id
        assert config["telegram"]["chat_id"] == 123


class TestMigrateConfig:
    """Tests for migrate_config function."""

    def test_applies_all_migrations(self, tmp_path: Path) -> None:
        """Test applies all applicable migrations."""
        config = {
            "workspace": {
                "name": "test",
                "bot_token": "token",
                "telegram_group_id": 123,
            },
            "repos": {"my-repo": {"path": "my-repo"}},
        }

        applied = migrate_config(config, config_path=tmp_path / "config.toml")

        assert "repos-to-folders" in applied
        assert "legacy-telegram" in applied
        assert len(applied) == 2

    def test_returns_empty_if_no_migrations_needed(self, tmp_path: Path) -> None:
        """Test returns empty list if config is already migrated."""
        config = {
            "workspace": {"name": "test"},
            "telegram": {
                "bot_token": "token",
                "chat_id": 123,
            },
            "folders": {},
        }

        applied = migrate_config(config, config_path=tmp_path / "config.toml")

        assert applied == []


class TestMigrateConfigFile:
    """Tests for migrate_config_file function."""

    def test_migrates_and_saves_file(self, tmp_path: Path) -> None:
        """Test migrates file and saves changes."""
        data = {
            "workspace": {
                "name": "test",
                "bot_token": "token",
                "telegram_group_id": 123,
            },
            "repos": {"my-repo": {"path": "my-repo"}},
        }
        config_path = _write_config(data, tmp_path)

        applied = migrate_config_file(config_path)

        assert "repos-to-folders" in applied
        assert "legacy-telegram" in applied

        # Verify file was updated
        updated = read_raw_toml(config_path)
        assert "folders" in updated
        assert "repos" not in updated
        assert "telegram" in updated
        assert updated["telegram"]["bot_token"] == "token"
        assert updated["telegram"]["chat_id"] == 123

    def test_creates_backup(self, tmp_path: Path) -> None:
        """Test creates backup before migration."""
        data = {
            "workspace": {
                "name": "test",
                "bot_token": "token",
                "telegram_group_id": 123,
            },
        }
        config_path = _write_config(data, tmp_path)

        migrate_config_file(config_path)

        # Verify backup was created
        backup_path = config_path.with_suffix(".toml.bak")
        assert backup_path.exists()

        # Backup should have original content
        backup_data = read_raw_toml(backup_path)
        assert backup_data["workspace"]["bot_token"] == "token"
        assert backup_data["workspace"]["telegram_group_id"] == 123

    def test_returns_empty_if_no_migrations(self, tmp_path: Path) -> None:
        """Test returns empty list if no migrations needed."""
        data = {
            "workspace": {"name": "test"},
            "telegram": {
                "bot_token": "token",
                "chat_id": 123,
            },
        }
        config_path = _write_config(data, tmp_path)

        applied = migrate_config_file(config_path)

        assert applied == []

        # Verify no backup was created
        backup_path = config_path.with_suffix(".toml.bak")
        assert not backup_path.exists()

    def test_returns_empty_if_file_not_found(self, tmp_path: Path) -> None:
        """Test returns empty list if file doesn't exist."""
        config_path = tmp_path / "nonexistent.toml"

        applied = migrate_config_file(config_path)

        assert applied == []

    def test_returns_empty_on_read_error(self, tmp_path: Path) -> None:
        """Test returns empty list on TOML parse error."""
        config_dir = tmp_path / WORKSPACE_CONFIG_DIR
        config_dir.mkdir()
        config_path = config_dir / WORKSPACE_CONFIG_FILE
        config_path.write_text("invalid { toml")

        applied = migrate_config_file(config_path)

        assert applied == []
