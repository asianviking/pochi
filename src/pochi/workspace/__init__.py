"""Workspace module for multi-folder support with Telegram topics."""

from .config import (
    WorkspaceConfig,
    FolderConfig,
    RalphConfig,
    load_workspace_config,
    save_workspace_config,
    create_workspace,
    find_workspace_root,
)
from .router import WorkspaceRouter, RouteResult, is_general_slash_command
from .manager import WorkspaceManager
from .commands import handle_slash_command
from .bridge import (
    WorkspaceBridgeConfig,
    run_workspace_loop,
    handle_workspace_message,
)
from .orchestrator import build_orchestrator_context, prepend_orchestrator_context
from .ralph import RalphManager, RalphLoop, parse_ralph_command

__all__ = [
    # Config
    "WorkspaceConfig",
    "FolderConfig",
    "RalphConfig",
    "load_workspace_config",
    "save_workspace_config",
    "create_workspace",
    "find_workspace_root",
    # Router
    "WorkspaceRouter",
    "RouteResult",
    "is_general_slash_command",
    # Manager
    "WorkspaceManager",
    # Commands
    "handle_slash_command",
    # Bridge
    "WorkspaceBridgeConfig",
    "run_workspace_loop",
    "handle_workspace_message",
    # Orchestrator
    "build_orchestrator_context",
    "prepend_orchestrator_context",
    # Ralph
    "RalphManager",
    "RalphLoop",
    "parse_ralph_command",
]
