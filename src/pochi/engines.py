"""Engine backend discovery and management.

Engines are discovered via Python entrypoints (pochi.engine_backends group).
This replaces the previous pkgutil-based discovery from runners/ modules.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .backends import EngineBackend, EngineConfig
from .logging import get_logger
from .plugins import (
    discover_engine_plugins,
    load_plugin,
)
from .settings import ConfigError

logger = get_logger(__name__)


def _discover_backends(
    *,
    enabled_distributions: set[str] | None = None,
) -> dict[str, EngineBackend]:
    """Discover and load all engine backends via entrypoints.

    Args:
        enabled_distributions: If set, only load plugins from these distributions.
            If None or empty, load all plugins.

    Returns:
        Dict mapping engine ID to EngineBackend.
    """
    discovery = discover_engine_plugins()
    backends: dict[str, EngineBackend] = {}

    # Log any discovery errors
    for error in discovery.errors:
        logger.warning("engine.discovery.error", error=error)

    for entry in discovery.entries:
        # Check enabled filter
        if enabled_distributions:
            dist_lower = (entry.distribution or "").lower()
            if not any(
                dist_lower == enabled.lower() for enabled in enabled_distributions
            ):
                continue

        loaded = load_plugin(entry)

        if loaded.error:
            logger.warning(
                "engine.load.error",
                engine=entry.id,
                error=loaded.error,
            )
            continue

        backends[loaded.id] = loaded.backend
        logger.debug(
            "engine.loaded",
            engine=loaded.id,
            distribution=loaded.distribution,
        )

    return backends


@cache
def _backends() -> Mapping[str, EngineBackend]:
    """Return cached mapping of all discovered backends."""
    return MappingProxyType(_discover_backends())


def get_backend(engine_id: str) -> EngineBackend:
    """Get a backend by ID."""
    backends = _backends()
    if engine_id not in backends:
        available = ", ".join(sorted(backends.keys())) or "(none)"
        raise ConfigError(
            f"Unknown engine {engine_id!r}. Available engines: {available}"
        )
    return backends[engine_id]


def list_backends() -> list[EngineBackend]:
    """List all available backends."""
    return list(_backends().values())


def list_backend_ids() -> list[str]:
    """List all backend IDs."""
    return list(_backends().keys())


def get_engine_config(
    config: dict[str, Any], engine_id: str, config_path: Path
) -> EngineConfig:
    """Get engine configuration from config dict."""
    engine_cfg = config.get(engine_id) or {}
    if not isinstance(engine_cfg, dict):
        raise ConfigError(
            f"Invalid `{engine_id}` config in {config_path}; expected a table."
        )
    return engine_cfg


def clear_engine_cache() -> None:
    """Clear the engine backend cache (for testing)."""
    _backends.cache_clear()
