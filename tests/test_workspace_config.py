"""Tests for pochi.workspace.config module."""

from __future__ import annotations

from pathlib import Path

import tomlkit

from pochi.workspace.config import (
    FolderConfig,
    RalphConfig,
    WorkspaceConfig,
    add_folder_to_workspace,
    create_workspace,
    find_workspace_root,
    load_workspace_config,
    save_workspace_config,
    update_folder_topic_id,
    WORKSPACE_CONFIG_DIR,
    WORKSPACE_CONFIG_FILE,
)


def _write_config_from_dict(data: dict, tmp_path: Path) -> Path:
    """Helper to write config dict to TOML file for testing."""
    config_dir = tmp_path / WORKSPACE_CONFIG_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / WORKSPACE_CONFIG_FILE
    config_path.write_text(tomlkit.dumps(data))
    return config_path


class TestFolderConfig:
    """Tests for FolderConfig dataclass."""

    def test_absolute_path(self, tmp_path: Path) -> None:
        """Test FolderConfig.absolute_path returns correct path."""
        folder = FolderConfig(name="test-repo", path="repos/test-repo")
        abs_path = folder.absolute_path(tmp_path)
        assert abs_path == tmp_path / "repos/test-repo"

    def test_is_git_repo_true(self, tmp_path: Path) -> None:
        """Test FolderConfig.is_git_repo returns True for git repos."""
        folder = FolderConfig(name="test-repo", path="test-repo")
        repo_dir = tmp_path / "test-repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()
        assert folder.is_git_repo(tmp_path) is True

    def test_is_git_repo_false(self, tmp_path: Path) -> None:
        """Test FolderConfig.is_git_repo returns False for non-git dirs."""
        folder = FolderConfig(name="test-repo", path="test-repo")
        repo_dir = tmp_path / "test-repo"
        repo_dir.mkdir()
        assert folder.is_git_repo(tmp_path) is False


class TestRalphConfig:
    """Tests for RalphConfig dataclass."""

    def test_defaults(self) -> None:
        """Test RalphConfig default values."""
        ralph = RalphConfig()
        assert ralph.enabled is False
        assert ralph.default_max_iterations == 3


class TestWorkspaceConfig:
    """Tests for WorkspaceConfig dataclass."""

    def test_get_folder_by_topic_found(self, tmp_path: Path) -> None:
        """Test get_folder_by_topic returns folder when found."""
        folder = FolderConfig(name="test", path="test", topic_id=123)
        config = WorkspaceConfig(
            name="test-workspace",
            root=tmp_path,
            telegram_group_id=1234,
            bot_token="token",
            folders={"test": folder},
        )
        result = config.get_folder_by_topic(123)
        assert result is folder

    def test_get_folder_by_topic_not_found(self, tmp_path: Path) -> None:
        """Test get_folder_by_topic returns None when not found."""
        config = WorkspaceConfig(
            name="test-workspace",
            root=tmp_path,
            telegram_group_id=1234,
            bot_token="token",
        )
        result = config.get_folder_by_topic(999)
        assert result is None

    def test_get_pending_topics(self, tmp_path: Path) -> None:
        """Test get_pending_topics returns folders with pending_topic=True."""
        folder1 = FolderConfig(
            name="done", path="done", topic_id=123, pending_topic=False
        )
        folder2 = FolderConfig(name="pending", path="pending", pending_topic=True)
        config = WorkspaceConfig(
            name="test-workspace",
            root=tmp_path,
            telegram_group_id=1234,
            bot_token="token",
            folders={"done": folder1, "pending": folder2},
        )
        pending = config.get_pending_topics()
        assert len(pending) == 1
        assert pending[0].name == "pending"

    def test_config_path(self, tmp_path: Path) -> None:
        """Test config_path returns correct path."""
        config = WorkspaceConfig(
            name="test-workspace",
            root=tmp_path,
            telegram_group_id=1234,
            bot_token="token",
        )
        expected = tmp_path / WORKSPACE_CONFIG_DIR / WORKSPACE_CONFIG_FILE
        assert config.config_path() == expected


class TestFindWorkspaceRoot:
    """Tests for find_workspace_root function."""

    def test_finds_workspace_in_current_dir(self, tmp_path: Path) -> None:
        """Test find_workspace_root finds workspace in current directory."""
        config_dir = tmp_path / WORKSPACE_CONFIG_DIR
        config_dir.mkdir()
        (config_dir / WORKSPACE_CONFIG_FILE).write_text("[workspace]\nname='test'")
        result = find_workspace_root(tmp_path)
        assert result == tmp_path

    def test_finds_workspace_in_parent(self, tmp_path: Path) -> None:
        """Test find_workspace_root finds workspace in parent directory."""
        config_dir = tmp_path / WORKSPACE_CONFIG_DIR
        config_dir.mkdir()
        (config_dir / WORKSPACE_CONFIG_FILE).write_text("[workspace]\nname='test'")
        child_dir = tmp_path / "subdir"
        child_dir.mkdir()
        result = find_workspace_root(child_dir)
        assert result == tmp_path

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        """Test find_workspace_root returns None when no workspace found."""
        result = find_workspace_root(tmp_path)
        assert result is None


class TestLoadWorkspaceConfig:
    """Tests for load_workspace_config function."""

    def test_loads_valid_config(self, tmp_path: Path) -> None:
        """Test load_workspace_config loads a valid config file."""
        config_dir = tmp_path / WORKSPACE_CONFIG_DIR
        config_dir.mkdir()
        config_content = """
[workspace]
name = "my-workspace"
telegram_group_id = 123456
bot_token = "test-token"
default_engine = "claude"

[folders.frontend]
path = "frontend"
topic_id = 100

[workers.ralph]
enabled = true
default_max_iterations = 5
"""
        (config_dir / WORKSPACE_CONFIG_FILE).write_text(config_content)
        config = load_workspace_config(tmp_path)
        assert config is not None
        assert config.name == "my-workspace"
        assert config.telegram_group_id == 123456
        assert config.bot_token == "test-token"
        assert config.default_engine == "claude"
        assert "frontend" in config.folders
        assert config.folders["frontend"].topic_id == 100
        assert config.ralph.enabled is True
        assert config.ralph.default_max_iterations == 5

    def test_returns_none_for_missing_config(self, tmp_path: Path) -> None:
        """Test load_workspace_config returns None when config doesn't exist."""
        result = load_workspace_config(tmp_path)
        assert result is None

    def test_returns_none_for_invalid_toml(self, tmp_path: Path) -> None:
        """Test load_workspace_config returns None for invalid TOML."""
        config_dir = tmp_path / WORKSPACE_CONFIG_DIR
        config_dir.mkdir()
        (config_dir / WORKSPACE_CONFIG_FILE).write_text("invalid { toml")
        result = load_workspace_config(tmp_path)
        assert result is None

    def test_auto_finds_workspace_root(self, tmp_path: Path, monkeypatch) -> None:
        """Test load_workspace_config auto-finds workspace root when not provided."""
        import os

        # Create config in tmp_path
        config_dir = tmp_path / WORKSPACE_CONFIG_DIR
        config_dir.mkdir()
        config_content = "[workspace]\nname = 'auto-test'\n"
        (config_dir / WORKSPACE_CONFIG_FILE).write_text(config_content)

        # Create and change to subdirectory
        sub_dir = tmp_path / "subdir"
        sub_dir.mkdir()

        # Change current directory to subdirectory
        original_cwd = os.getcwd()
        try:
            os.chdir(sub_dir)
            # Now call without workspace_root - should auto-find
            config = load_workspace_config()
            assert config is not None
            assert config.name == "auto-test"
        finally:
            os.chdir(original_cwd)

    def test_returns_none_when_auto_find_fails(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Test load_workspace_config returns None when auto-find fails."""
        import os

        # Change to a directory without workspace
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            # Should return None since no workspace config
            config = load_workspace_config()
            assert config is None
        finally:
            os.chdir(original_cwd)


class TestParseWorkspaceConfig:
    """Tests for config parsing via load_workspace_config."""

    def test_parses_minimal_config(self, tmp_path: Path) -> None:
        """Test parsing minimal config."""
        data: dict = {"workspace": {}}
        _write_config_from_dict(data, tmp_path)
        config = load_workspace_config(tmp_path)
        assert config is not None
        assert config.name == tmp_path.name
        assert config.telegram_group_id == 0
        assert config.bot_token == ""
        assert config.default_engine == "claude"

    def test_parses_folders_section(self, tmp_path: Path) -> None:
        """Test parsing folders section."""
        data = {
            "workspace": {"name": "test"},
            "folders": {
                "backend": {
                    "path": "backend",
                    "topic_id": 200,
                    "description": "Backend service",
                    "origin": "git@github.com:user/backend.git",
                    "pending_topic": True,
                }
            },
        }
        _write_config_from_dict(data, tmp_path)
        config = load_workspace_config(tmp_path)
        assert config is not None
        assert "backend" in config.folders
        folder = config.folders["backend"]
        assert folder.path == "backend"
        assert folder.topic_id == 200
        assert folder.description == "Backend service"
        assert folder.origin == "git@github.com:user/backend.git"
        assert folder.pending_topic is True

    def test_migrates_legacy_repos_section(self, tmp_path: Path) -> None:
        """Test parsing migrates legacy [repos] section."""
        data = {
            "workspace": {"name": "test"},
            "repos": {
                "legacy-repo": {
                    "path": "legacy",
                    "topic_id": 300,
                }
            },
        }
        _write_config_from_dict(data, tmp_path)
        config = load_workspace_config(tmp_path)
        assert config is not None
        assert "legacy-repo" in config.folders
        assert config.folders["legacy-repo"].topic_id == 300


class TestSaveWorkspaceConfig:
    """Tests for save_workspace_config function."""

    def test_saves_config(self, tmp_path: Path) -> None:
        """Test save_workspace_config writes valid config file."""
        folder = FolderConfig(
            name="test-folder",
            path="test-folder",
            topic_id=123,
            description="Test description",
            origin="git@github.com:user/repo.git",
            pending_topic=True,
        )
        ralph = RalphConfig(enabled=True, default_max_iterations=10)
        config = WorkspaceConfig(
            name="saved-workspace",
            root=tmp_path,
            telegram_group_id=999,
            bot_token="secret-token",
            folders={"test-folder": folder},
            ralph=ralph,
            default_engine="codex",
        )
        save_workspace_config(config)

        # Verify the file was written
        config_path = tmp_path / WORKSPACE_CONFIG_DIR / WORKSPACE_CONFIG_FILE
        assert config_path.exists()

        # Load it back and verify
        loaded = load_workspace_config(tmp_path)
        assert loaded is not None
        assert loaded.name == "saved-workspace"
        assert loaded.telegram_group_id == 999
        assert loaded.bot_token == "secret-token"
        assert loaded.default_engine == "codex"
        assert "test-folder" in loaded.folders
        assert loaded.folders["test-folder"].topic_id == 123
        assert loaded.folders["test-folder"].description == "Test description"
        assert loaded.ralph.enabled is True
        assert loaded.ralph.default_max_iterations == 10


class TestCreateWorkspace:
    """Tests for create_workspace function."""

    def test_creates_workspace(self, tmp_path: Path) -> None:
        """Test create_workspace creates a new workspace config."""
        config = create_workspace(
            root=tmp_path,
            name="new-workspace",
            telegram_group_id=12345,
            bot_token="new-token",
        )
        assert config.name == "new-workspace"
        assert config.telegram_group_id == 12345
        assert config.bot_token == "new-token"
        assert config.root == tmp_path.resolve()

        # Verify file was created
        config_path = tmp_path / WORKSPACE_CONFIG_DIR / WORKSPACE_CONFIG_FILE
        assert config_path.exists()


class TestAddFolderToWorkspace:
    """Tests for add_folder_to_workspace function."""

    def test_adds_folder(self, tmp_path: Path) -> None:
        """Test add_folder_to_workspace adds a folder and saves config."""
        config = create_workspace(
            root=tmp_path,
            name="test-workspace",
            telegram_group_id=123,
            bot_token="token",
        )
        folder = add_folder_to_workspace(
            config,
            name="new-folder",
            path="new-folder",
            description="A new folder",
            origin="git@github.com:user/repo.git",
            pending_topic=True,
        )
        assert folder.name == "new-folder"
        assert folder.path == "new-folder"
        assert folder.description == "A new folder"
        assert folder.origin == "git@github.com:user/repo.git"
        assert folder.pending_topic is True
        assert "new-folder" in config.folders


class TestUpdateFolderTopicId:
    """Tests for update_folder_topic_id function."""

    def test_updates_topic_id(self, tmp_path: Path) -> None:
        """Test update_folder_topic_id updates topic_id and clears pending."""
        config = create_workspace(
            root=tmp_path,
            name="test-workspace",
            telegram_group_id=123,
            bot_token="token",
        )
        add_folder_to_workspace(config, "folder", "folder", pending_topic=True)
        assert config.folders["folder"].pending_topic is True
        assert config.folders["folder"].topic_id is None

        update_folder_topic_id(config, "folder", 456)

        assert config.folders["folder"].topic_id == 456
        assert config.folders["folder"].pending_topic is False

    def test_ignores_nonexistent_folder(self, tmp_path: Path) -> None:
        """Test update_folder_topic_id does nothing for nonexistent folder."""
        config = create_workspace(
            root=tmp_path,
            name="test-workspace",
            telegram_group_id=123,
            bot_token="token",
        )
        # Should not raise
        update_folder_topic_id(config, "nonexistent", 999)


class TestTransportsConfig:
    """Tests for new [transports.<id>] config format."""

    def test_loads_transports_telegram_section(self, tmp_path: Path) -> None:
        """Test loading new [transports.telegram] format."""
        data = {
            "workspace": {"name": "test"},
            "transports": {
                "telegram": {
                    "bot_token": "new-format-token",
                    "chat_id": 987654,
                }
            },
        }
        _write_config_from_dict(data, tmp_path)
        config = load_workspace_config(tmp_path)
        assert config is not None
        assert config.transports == {
            "telegram": {"bot_token": "new-format-token", "chat_id": 987654}
        }

    def test_transports_telegram_populates_legacy_fields(self, tmp_path: Path) -> None:
        """Test that [transports.telegram] populates legacy telegram_group_id and bot_token.

        This is important for backward compatibility - many parts of the codebase
        still use config.telegram_group_id and config.bot_token.
        """
        data = {
            "workspace": {"name": "test"},
            "transports": {
                "telegram": {
                    "bot_token": "transports-token",
                    "chat_id": 123456789,
                }
            },
        }
        _write_config_from_dict(data, tmp_path)
        config = load_workspace_config(tmp_path)
        assert config is not None
        # Legacy fields should be populated from transports.telegram
        assert config.telegram_group_id == 123456789
        assert config.bot_token == "transports-token"

    def test_legacy_telegram_section_takes_precedence(self, tmp_path: Path) -> None:
        """Test that legacy [telegram] section takes precedence over [transports.telegram]."""
        data = {
            "workspace": {"name": "test"},
            "telegram": {
                "bot_token": "legacy-section-token",
                "chat_id": 111111,
            },
            "transports": {
                "telegram": {
                    "bot_token": "transports-token",
                    "chat_id": 222222,
                }
            },
        }
        _write_config_from_dict(data, tmp_path)
        config = load_workspace_config(tmp_path)
        assert config is not None
        # Legacy [telegram] section should take precedence
        assert config.telegram_group_id == 111111
        assert config.bot_token == "legacy-section-token"

    def test_transport_config_method(self, tmp_path: Path) -> None:
        """Test WorkspaceConfig.transport_config() method."""
        data = {
            "workspace": {"name": "test"},
            "transports": {
                "telegram": {
                    "bot_token": "my-token",
                    "chat_id": 12345,
                }
            },
        }
        _write_config_from_dict(data, tmp_path)
        config = load_workspace_config(tmp_path)
        assert config is not None
        tc = config.transport_config("telegram")
        assert tc["bot_token"] == "my-token"
        assert tc["chat_id"] == 12345
