"""Plugin ID validation and reserved ID management.

Plugin IDs are used in the CLI and in Telegram commands.
They must match the ID pattern and not conflict with reserved IDs.
"""

from __future__ import annotations

import re
from typing import Literal

# Plugin ID must be lowercase alphanumeric with underscores, 1-32 chars
ID_PATTERN = re.compile(r"^[a-z0-9_]{1,32}$")

PluginKind = Literal["engine", "transport", "command"]

# Reserved IDs for engines - these cannot be used as engine plugin IDs
# because they conflict with core commands or CLI commands
RESERVED_ENGINE_IDS: frozenset[str] = frozenset(
    {
        "cancel",  # Core chat command
        "help",  # Core chat command
        "init",  # CLI command
        "plugins",  # CLI command
        "info",  # CLI command
        "setup",  # CLI command
    }
)

# Reserved IDs for commands - these cannot be used as command plugin IDs
# because they conflict with built-in workspace commands
RESERVED_COMMAND_IDS: frozenset[str] = frozenset(
    {
        # Core commands
        "cancel",
        "help",
        # Workspace management commands
        "clone",
        "create",
        "add",
        "list",
        "remove",
        "status",
        "engine",
        # Worker topic commands
        "ralph",
    }
)

# Reserved IDs for transports - currently none, but structure exists
RESERVED_TRANSPORT_IDS: frozenset[str] = frozenset()


def is_valid_id(plugin_id: str) -> bool:
    """Check if a plugin ID matches the required pattern.

    IDs must be 1-32 lowercase alphanumeric characters or underscores.
    """
    return bool(ID_PATTERN.match(plugin_id))


def is_reserved_id(plugin_id: str, kind: PluginKind) -> bool:
    """Check if a plugin ID is reserved for a given plugin kind."""
    if kind == "engine":
        return plugin_id in RESERVED_ENGINE_IDS
    if kind == "transport":
        return plugin_id in RESERVED_TRANSPORT_IDS
    if kind == "command":
        return plugin_id in RESERVED_COMMAND_IDS
    return False


def validate_plugin_id(
    plugin_id: str,
    kind: PluginKind,
    *,
    context: str = "",
) -> tuple[bool, str | None]:
    """Validate a plugin ID for a given kind.

    Args:
        plugin_id: The ID to validate
        kind: The plugin kind (engine, transport, command)
        context: Optional context string for error messages (e.g., entrypoint name)

    Returns:
        Tuple of (is_valid, error_message).
        If valid, error_message is None.
    """
    ctx = f" ({context})" if context else ""

    if not is_valid_id(plugin_id):
        return False, (
            f"Invalid {kind} ID '{plugin_id}'{ctx}: "
            f"must match pattern {ID_PATTERN.pattern}"
        )

    if is_reserved_id(plugin_id, kind):
        return False, f"Reserved {kind} ID '{plugin_id}'{ctx}: conflicts with built-in"

    return True, None


def get_reserved_ids(kind: PluginKind) -> frozenset[str]:
    """Get the set of reserved IDs for a given plugin kind."""
    if kind == "engine":
        return RESERVED_ENGINE_IDS
    if kind == "transport":
        return RESERVED_TRANSPORT_IDS
    if kind == "command":
        return RESERVED_COMMAND_IDS
    return frozenset()
