from __future__ import annotations

import importlib
import pkgutil
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .backends import EngineBackend, EngineConfig
from .config import ConfigError


def _discover_backends() -> dict[str, EngineBackend]:
    """Discover all backends from runners/ modules.

    Each runner module can export a BACKEND constant of type EngineBackend.
    """
    import pochi.runners as runners_pkg

    backends: dict[str, EngineBackend] = {}
    prefix = runners_pkg.__name__ + "."

    for module_info in pkgutil.iter_modules(runners_pkg.__path__, prefix):
        try:
            mod = importlib.import_module(module_info.name)
        except ImportError:
            continue

        backend = getattr(mod, "BACKEND", None)
        if backend is None:
            continue
        if not isinstance(backend, EngineBackend):
            raise RuntimeError(f"{module_info.name}.BACKEND is not an EngineBackend")
        backends[backend.id] = backend

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
