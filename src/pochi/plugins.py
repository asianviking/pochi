"""Plugin discovery via Python entrypoints.

Pochi supports three types of plugins:
- Engine backends (pochi.engine_backends): New AI engine runners
- Transport backends (pochi.transport_backends): New messaging platforms
- Command backends (pochi.command_backends): Custom /command handlers

Plugins are discovered lazily: IDs are listed without importing until needed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from functools import cache
from typing import TYPE_CHECKING, Any, Literal

from .ids import PluginKind, validate_plugin_id
from .logging import get_logger

if TYPE_CHECKING:
    from importlib.metadata import EntryPoint

    from .backends import EngineBackend
    from .command_backend import CommandBackend
    from .transport_backend import TransportBackend

logger = get_logger(__name__)

# Entrypoint group names
ENGINE_BACKENDS_GROUP = "pochi.engine_backends"
TRANSPORT_BACKENDS_GROUP = "pochi.transport_backends"
COMMAND_BACKENDS_GROUP = "pochi.command_backends"

PluginType = Literal["engine", "transport", "command"]


@dataclass(frozen=True, slots=True)
class PluginEntry:
    """A discovered plugin entrypoint (not yet loaded)."""

    id: str
    entrypoint: "EntryPoint"
    kind: PluginKind
    distribution: str | None = None  # Package name if resolvable


@dataclass(slots=True)
class LoadedPlugin:
    """A loaded and validated plugin."""

    id: str
    kind: PluginKind
    backend: Any  # EngineBackend | TransportBackend | CommandBackend
    distribution: str | None = None
    error: str | None = None  # If loading failed


@dataclass(slots=True)
class PluginDiscoveryResult:
    """Result of plugin discovery for one entrypoint group."""

    kind: PluginKind
    entries: list[PluginEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PluginLoadResult:
    """Result of loading all plugins from discovery."""

    engine_backends: dict[str, "EngineBackend"] = field(default_factory=dict)
    transport_backends: dict[str, "TransportBackend"] = field(default_factory=dict)
    command_backends: dict[str, "CommandBackend"] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _get_distribution_name(entrypoint: "EntryPoint") -> str | None:
    """Get the distribution (package) name for an entrypoint."""
    # In Python 3.10+, entry_points have a .dist attribute
    try:
        dist = entrypoint.dist
        if dist is not None:
            return dist.name
    except AttributeError:
        pass

    # Fallback: try to get from group metadata
    try:
        return entrypoint.group
    except AttributeError:
        pass

    return None


def _discover_entrypoints(group: str, kind: PluginKind) -> PluginDiscoveryResult:
    """Discover plugins from an entrypoint group without loading them."""
    if sys.version_info >= (3, 10):
        from importlib.metadata import entry_points

        eps = entry_points(group=group)
    else:
        from importlib.metadata import entry_points

        all_eps = entry_points()
        eps = all_eps.get(group, [])

    result = PluginDiscoveryResult(kind=kind)

    for ep in eps:
        plugin_id = ep.name
        dist_name = _get_distribution_name(ep)

        # Validate ID
        valid, error = validate_plugin_id(plugin_id, kind, context=ep.value)
        if not valid:
            result.errors.append(error or f"Invalid ID: {plugin_id}")
            continue

        entry = PluginEntry(
            id=plugin_id,
            entrypoint=ep,
            kind=kind,
            distribution=dist_name,
        )
        result.entries.append(entry)

    return result


def discover_engine_plugins() -> PluginDiscoveryResult:
    """Discover engine backend plugins without loading them."""
    return _discover_entrypoints(ENGINE_BACKENDS_GROUP, "engine")


def discover_transport_plugins() -> PluginDiscoveryResult:
    """Discover transport backend plugins without loading them."""
    return _discover_entrypoints(TRANSPORT_BACKENDS_GROUP, "transport")


def discover_command_plugins() -> PluginDiscoveryResult:
    """Discover command backend plugins without loading them."""
    return _discover_entrypoints(COMMAND_BACKENDS_GROUP, "command")


def discover_all_plugins() -> dict[PluginKind, PluginDiscoveryResult]:
    """Discover all plugins without loading them."""
    return {
        "engine": discover_engine_plugins(),
        "transport": discover_transport_plugins(),
        "command": discover_command_plugins(),
    }


def _load_engine_backend(entry: PluginEntry) -> LoadedPlugin:
    """Load and validate an engine backend plugin."""
    from .backends import EngineBackend

    try:
        backend = entry.entrypoint.load()
    except Exception as exc:
        return LoadedPlugin(
            id=entry.id,
            kind=entry.kind,
            backend=None,
            distribution=entry.distribution,
            error=f"Failed to load: {exc}",
        )

    if not isinstance(backend, EngineBackend):
        return LoadedPlugin(
            id=entry.id,
            kind=entry.kind,
            backend=None,
            distribution=entry.distribution,
            error=f"Expected EngineBackend, got {type(backend).__name__}",
        )

    if backend.id != entry.id:
        return LoadedPlugin(
            id=entry.id,
            kind=entry.kind,
            backend=None,
            distribution=entry.distribution,
            error=f"ID mismatch: entrypoint '{entry.id}' != backend.id '{backend.id}'",
        )

    return LoadedPlugin(
        id=entry.id,
        kind=entry.kind,
        backend=backend,
        distribution=entry.distribution,
    )


def _load_transport_backend(entry: PluginEntry) -> LoadedPlugin:
    """Load and validate a transport backend plugin."""
    try:
        backend = entry.entrypoint.load()
    except Exception as exc:
        return LoadedPlugin(
            id=entry.id,
            kind=entry.kind,
            backend=None,
            distribution=entry.distribution,
            error=f"Failed to load: {exc}",
        )

    # Check if it has the required protocol attributes
    if not hasattr(backend, "id") or not hasattr(backend, "check_setup"):
        return LoadedPlugin(
            id=entry.id,
            kind=entry.kind,
            backend=None,
            distribution=entry.distribution,
            error="Does not implement TransportBackend protocol",
        )

    if backend.id != entry.id:
        return LoadedPlugin(
            id=entry.id,
            kind=entry.kind,
            backend=None,
            distribution=entry.distribution,
            error=f"ID mismatch: entrypoint '{entry.id}' != backend.id '{backend.id}'",
        )

    return LoadedPlugin(
        id=entry.id,
        kind=entry.kind,
        backend=backend,
        distribution=entry.distribution,
    )


def _load_command_backend(entry: PluginEntry) -> LoadedPlugin:
    """Load and validate a command backend plugin."""
    try:
        backend = entry.entrypoint.load()
    except Exception as exc:
        return LoadedPlugin(
            id=entry.id,
            kind=entry.kind,
            backend=None,
            distribution=entry.distribution,
            error=f"Failed to load: {exc}",
        )

    # Check if it has the required protocol attributes
    if not hasattr(backend, "id") or not hasattr(backend, "handle"):
        return LoadedPlugin(
            id=entry.id,
            kind=entry.kind,
            backend=None,
            distribution=entry.distribution,
            error="Does not implement CommandBackend protocol",
        )

    if backend.id != entry.id:
        return LoadedPlugin(
            id=entry.id,
            kind=entry.kind,
            backend=None,
            distribution=entry.distribution,
            error=f"ID mismatch: entrypoint '{entry.id}' != backend.id '{backend.id}'",
        )

    return LoadedPlugin(
        id=entry.id,
        kind=entry.kind,
        backend=backend,
        distribution=entry.distribution,
    )


def load_plugin(entry: PluginEntry) -> LoadedPlugin:
    """Load and validate a single plugin."""
    if entry.kind == "engine":
        return _load_engine_backend(entry)
    if entry.kind == "transport":
        return _load_transport_backend(entry)
    if entry.kind == "command":
        return _load_command_backend(entry)
    return LoadedPlugin(
        id=entry.id,
        kind=entry.kind,
        backend=None,
        distribution=entry.distribution,
        error=f"Unknown plugin kind: {entry.kind}",
    )


def load_all_plugins(
    *,
    enabled_distributions: set[str] | None = None,
) -> PluginLoadResult:
    """Load all discovered plugins.

    Args:
        enabled_distributions: If set, only load plugins from these distributions.
            If None or empty, load all plugins.

    Returns:
        PluginLoadResult with loaded backends and any errors.
    """
    result = PluginLoadResult()
    discovery = discover_all_plugins()

    for kind, disc_result in discovery.items():
        # Add discovery errors
        result.errors.extend(disc_result.errors)

        for entry in disc_result.entries:
            # Check enabled filter
            if enabled_distributions:
                dist_lower = (entry.distribution or "").lower()
                if not any(
                    dist_lower == enabled.lower() for enabled in enabled_distributions
                ):
                    continue

            loaded = load_plugin(entry)

            if loaded.error:
                result.errors.append(f"{kind}/{loaded.id}: {loaded.error}")
                continue

            if kind == "engine":
                result.engine_backends[loaded.id] = loaded.backend
            elif kind == "transport":
                result.transport_backends[loaded.id] = loaded.backend
            elif kind == "command":
                result.command_backends[loaded.id] = loaded.backend

    return result


@cache
def _cached_engine_backends() -> dict[str, "EngineBackend"]:
    """Cached loading of engine backends."""
    result = load_all_plugins()
    for error in result.errors:
        logger.warning("plugin.error", error=error)
    return result.engine_backends


def get_engine_backends() -> dict[str, "EngineBackend"]:
    """Get all loaded engine backends (cached)."""
    return _cached_engine_backends()


def clear_plugin_cache() -> None:
    """Clear the plugin loading cache (for testing)."""
    _cached_engine_backends.cache_clear()
