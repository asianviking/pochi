"""Transport loading utilities for plugin-based transports.

This module provides functions for loading and managing transport backends
from the plugin system.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .logging import get_logger
from .plugins import (
    discover_transport_plugins,
    load_plugin,
)

if TYPE_CHECKING:
    from .config import WorkspaceConfig
    from .transport_backend import TransportBackend

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ConfiguredTransport:
    """A transport with its configuration."""

    transport_id: str
    backend: "TransportBackend"
    config: dict[str, Any]


class TransportNotFoundError(RuntimeError):
    """Raised when a transport backend cannot be found."""

    pass


class TransportLoadError(RuntimeError):
    """Raised when a transport backend fails to load."""

    pass


def get_transport(transport_id: str) -> "TransportBackend":
    """Load a transport backend by ID.

    Args:
        transport_id: The transport ID (e.g., "telegram", "discord")

    Returns:
        The loaded TransportBackend

    Raises:
        TransportNotFoundError: If no transport with that ID is discovered
        TransportLoadError: If the transport fails to load
    """
    discovery = discover_transport_plugins()

    # Find the entry for this transport
    entry = None
    for e in discovery.entries:
        if e.id == transport_id:
            entry = e
            break

    if entry is None:
        available = [e.id for e in discovery.entries]
        raise TransportNotFoundError(
            f"Transport '{transport_id}' not found. "
            f"Available transports: {', '.join(available) or 'none'}"
        )

    # Load the plugin
    loaded = load_plugin(entry)
    if loaded.error:
        raise TransportLoadError(
            f"Failed to load transport '{transport_id}': {loaded.error}"
        )

    return loaded.backend


def get_configured_transports(
    workspace_config: "WorkspaceConfig",
) -> list[ConfiguredTransport]:
    """Get all configured transports with their configuration.

    This finds all transport IDs that have configuration in the workspace,
    loads their backends, and returns them paired with their configs.

    Args:
        workspace_config: The workspace configuration

    Returns:
        List of ConfiguredTransport objects

    Raises:
        TransportNotFoundError: If a configured transport is not available
        TransportLoadError: If a transport fails to load
    """
    configured: list[ConfiguredTransport] = []

    # Get all transport IDs that have configuration
    transport_ids = workspace_config.configured_transport_ids()

    for transport_id in transport_ids:
        backend = get_transport(transport_id)
        config = workspace_config.transport_config(transport_id)

        configured.append(
            ConfiguredTransport(
                transport_id=transport_id,
                backend=backend,
                config=config,
            )
        )

    return configured


def list_available_transports() -> list[str]:
    """List all available transport IDs from the plugin system.

    Returns:
        List of transport IDs that can be loaded
    """
    discovery = discover_transport_plugins()
    return [e.id for e in discovery.entries]
