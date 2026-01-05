"""Tests for engine auto-discovery."""

import pytest

from pochi.backends import EngineBackend
from pochi.config import ConfigError
from pochi.engines import (
    get_backend,
    get_engine_config,
    list_backend_ids,
    list_backends,
)


def test_list_backends_discovers_claude() -> None:
    """Test that auto-discovery finds the Claude backend."""
    backends = list_backends()
    assert len(backends) >= 1

    # Claude should be discovered
    backend_ids = [b.id for b in backends]
    assert "claude" in backend_ids


def test_list_backend_ids_matches_backends() -> None:
    """Test that list_backend_ids returns IDs of all backends."""
    backends = list_backends()
    backend_ids = list_backend_ids()

    assert len(backends) == len(backend_ids)
    for backend in backends:
        assert backend.id in backend_ids


def test_get_backend_returns_claude() -> None:
    """Test that get_backend returns the Claude backend."""
    backend = get_backend("claude")
    assert isinstance(backend, EngineBackend)
    assert backend.id == "claude"
    assert callable(backend.build_runner)


def test_get_backend_raises_for_unknown() -> None:
    """Test that get_backend raises ConfigError for unknown engines."""
    with pytest.raises(ConfigError) as exc_info:
        get_backend("unknown_engine")

    assert "Unknown engine" in str(exc_info.value)
    assert "unknown_engine" in str(exc_info.value)


def test_get_engine_config_returns_dict() -> None:
    """Test that get_engine_config extracts engine config."""
    from pathlib import Path

    config = {"claude": {"model": "opus", "allowed_tools": ["Bash"]}}
    engine_cfg = get_engine_config(config, "claude", Path("/test"))

    assert engine_cfg == {"model": "opus", "allowed_tools": ["Bash"]}


def test_get_engine_config_returns_empty_for_missing() -> None:
    """Test that get_engine_config returns empty dict for missing engine."""
    from pathlib import Path

    config = {}
    engine_cfg = get_engine_config(config, "claude", Path("/test"))

    assert engine_cfg == {}


def test_get_engine_config_raises_for_invalid_type() -> None:
    """Test that get_engine_config raises for non-dict config."""
    from pathlib import Path

    config = {"claude": "not a dict"}

    with pytest.raises(ConfigError) as exc_info:
        get_engine_config(config, "claude", Path("/test/workspace.toml"))

    assert "Invalid `claude` config" in str(exc_info.value)
    assert "expected a table" in str(exc_info.value)


def test_claude_backend_has_install_cmd() -> None:
    """Test that Claude backend has install command."""
    backend = get_backend("claude")
    assert backend.install_cmd is not None
    assert "npm install" in backend.install_cmd
