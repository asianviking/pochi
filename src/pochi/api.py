"""Public Plugin API for Pochi.

This module exports the stable API surface for plugin authors.
Anything not imported from `pochi.api` should be considered internal
and subject to change.

Plugin authors should pin to a compatible Pochi version range:
    dependencies = ["pochi>=0.2,<0.3"]

Example plugin usage:
    from pochi.api import (
        EngineBackend,
        EngineConfig,
        CommandBackend,
        CommandContext,
        CommandResult,
    )
"""

from __future__ import annotations

# --- Engine backends and runners ---
from .backends import (
    EngineBackend,
    EngineConfig,
    SetupIssue,
)
from .command_backend import (
    CommandBackend,
    CommandContext,
    CommandExecutor,
    CommandResult,
    RunMode,
    RunRequest,
    RunResult,
)
from .events import EventFactory
from .model import (
    Action,
    ActionEvent,
    ActionKind,
    ActionLevel,
    ActionPhase,
    CompletedEvent,
    EngineId,
    PochiEvent,
    ResumeToken,
    StartedEvent,
)

# --- Router ---
from .router import (
    RunnerEntry,
    RunnerUnavailableError,
)
from .runner import (
    BaseRunner,
    JsonlRunState,
    JsonlSubprocessRunner,
    ResumeTokenMixin,
    Runner,
    SessionLockMixin,
)

# --- Configuration ---
from .settings import ConfigError

# --- Transport backends ---
from .transport_backend import (
    SetupResult,
    TransportBackend,
)
from .transport_runtime import (
    ResolvedMessage,
    ResolvedRunner,
    TransportRuntime,
)

# API version for compatibility tracking
POCHI_PLUGIN_API_VERSION = 1

# --- Exports ---

__all__ = [
    # Version
    "POCHI_PLUGIN_API_VERSION",
    # Engine types
    "EngineBackend",
    "EngineConfig",
    "EngineId",
    "Runner",
    "BaseRunner",
    "JsonlSubprocessRunner",
    "JsonlRunState",
    "ResumeTokenMixin",
    "SessionLockMixin",
    "RunnerEntry",
    "RunnerUnavailableError",
    # Event types
    "PochiEvent",
    "StartedEvent",
    "ActionEvent",
    "CompletedEvent",
    "Action",
    "ActionKind",
    "ActionPhase",
    "ActionLevel",
    "EventFactory",
    "ResumeToken",
    # Transport types
    "TransportBackend",
    "TransportRuntime",
    "SetupResult",
    "SetupIssue",
    "ResolvedMessage",
    "ResolvedRunner",
    # Command types
    "CommandBackend",
    "CommandContext",
    "CommandExecutor",
    "CommandResult",
    "RunRequest",
    "RunResult",
    "RunMode",
    # Config types
    "ConfigError",
]
