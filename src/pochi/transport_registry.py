"""Transport registry for multi-platform message delivery.

This module provides a plugin-based registry for transport backends,
enabling future support for backends beyond Telegram (e.g., Discord, Slack).

Each transport backend must implement the TransportBackend protocol and
be discoverable via the transports submodule (e.g., pochi.transports.telegram).
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .logging import get_logger
from .settings import ConfigError

if TYPE_CHECKING:
    from .config import WorkspaceConfig


logger = get_logger(__name__)

# Transport group name for discovery
TRANSPORT_GROUP = "pochi.transports"

# Default transport if not specified
DEFAULT_TRANSPORT = "telegram"


@dataclass(frozen=True, slots=True)
class SetupResult:
    """Result of checking transport setup."""

    ready: bool
    message: str = ""
    details: dict[str, Any] | None = None


@runtime_checkable
class TransportBackend(Protocol):
    """Protocol for transport backend implementations.

    Each transport backend module should export a TRANSPORT constant
    that implements this protocol.
    """

    id: str
    """Unique identifier for this transport (e.g., 'telegram')."""

    description: str
    """Human-readable description of this transport."""

    def check_setup(self, config: "WorkspaceConfig") -> SetupResult:
        """Check if this transport is properly configured.

        Args:
            config: Workspace configuration

        Returns:
            SetupResult indicating whether the transport is ready
        """
        ...

    def get_config_section(self) -> str:
        """Get the TOML section name for this transport's config.

        Returns:
            Section name (e.g., 'telegram' for [telegram] section)
        """
        ...


# Registry of loaded transport backends
_registry: dict[str, TransportBackend] = {}


def _load_transport(transport_id: str) -> TransportBackend | None:
    """Load a transport backend by ID.

    Args:
        transport_id: Transport identifier (e.g., 'telegram')

    Returns:
        TransportBackend if found and valid, None otherwise
    """
    if transport_id in _registry:
        return _registry[transport_id]

    module_name = f"pochi.transports.{transport_id}"
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        logger.debug(
            "transport.load_failed",
            transport=transport_id,
            error=str(e),
        )
        return None

    # Look for TRANSPORT constant
    transport = getattr(module, "TRANSPORT", None)
    if transport is None:
        logger.warning(
            "transport.no_backend",
            transport=transport_id,
            module=module_name,
        )
        return None

    # Validate it implements the protocol
    if not isinstance(transport, TransportBackend):
        logger.warning(
            "transport.invalid_backend",
            transport=transport_id,
            module=module_name,
        )
        return None

    _registry[transport_id] = transport
    logger.debug("transport.loaded", transport=transport_id)
    return transport


def get_transport(transport_id: str) -> TransportBackend:
    """Get a transport backend by ID.

    Args:
        transport_id: Transport identifier

    Returns:
        TransportBackend

    Raises:
        ConfigError: If transport not found
    """
    transport = _load_transport(transport_id)
    if transport is None:
        available = list_transports()
        available_str = ", ".join(available) if available else "none"
        raise ConfigError(
            f"Unknown transport '{transport_id}'. Available: {available_str}"
        )
    return transport


def list_transports() -> list[str]:
    """List available transport IDs.

    Returns:
        List of transport IDs that can be loaded
    """
    # Check for known transports
    known_transports = ["telegram"]
    available: list[str] = []

    for transport_id in known_transports:
        if _load_transport(transport_id) is not None:
            available.append(transport_id)

    return available


def check_transport_setup(transport_id: str, config: "WorkspaceConfig") -> SetupResult:
    """Check if a transport is properly configured.

    Args:
        transport_id: Transport identifier
        config: Workspace configuration

    Returns:
        SetupResult
    """
    try:
        transport = get_transport(transport_id)
    except ConfigError as e:
        return SetupResult(ready=False, message=str(e))

    return transport.check_setup(config)


def get_default_transport() -> str:
    """Get the default transport ID.

    Returns:
        Default transport ID ('telegram')
    """
    return DEFAULT_TRANSPORT
