"""Transport backends for multi-platform message delivery."""

from __future__ import annotations

from ..transport_registry import (
    TransportBackend,
    SetupResult,
    get_transport,
    list_transports,
    check_transport_setup,
    get_default_transport,
)

__all__ = [
    "TransportBackend",
    "SetupResult",
    "get_transport",
    "list_transports",
    "check_transport_setup",
    "get_default_transport",
]
