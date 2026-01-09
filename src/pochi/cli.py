from __future__ import annotations

import shutil
import tomllib
from pathlib import Path
from typing import Any

import anyio
import typer

from . import __version__
from .backends import EngineBackend
from .config import ConfigError
from .engines import get_engine_config, list_backends
from .logging import get_logger, setup_logging
from .router import AutoRouter, RunnerEntry
from .telegram import TelegramClient
from .workspace import (
    WorkspaceConfig,
    create_workspace,
    find_workspace_root,
    load_workspace_config,
)

logger = get_logger(__name__)


def _print_version_and_exit() -> None:
    typer.echo(__version__)
    raise typer.Exit()


def _version_callback(value: bool) -> None:
    if value:
        _print_version_and_exit()


def _load_raw_config(config_path: Path) -> dict[str, Any]:
    """Load raw TOML config for engine-specific sections."""
    if not config_path.exists():
        return {}
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def _build_runner_entry(
    backend: EngineBackend,
    raw_config: dict[str, Any],
    config_path: Path,
) -> RunnerEntry:
    """Build a RunnerEntry for a single backend."""
    engine_cfg = get_engine_config(raw_config, backend.id, config_path)
    cmd = backend.cli_cmd or backend.id

    # Check CLI availability
    if shutil.which(cmd) is None:
        # Return unavailable entry
        return RunnerEntry(
            engine=backend.id,
            runner=None,  # type: ignore[arg-type]
            available=False,
            issue=f"{cmd} not found on PATH",
        )

    try:
        runner = backend.build_runner(engine_cfg, config_path)
    except Exception as exc:
        return RunnerEntry(
            engine=backend.id,
            runner=None,  # type: ignore[arg-type]
            available=False,
            issue=f"Failed to build runner: {exc}",
        )

    return RunnerEntry(
        engine=backend.id,
        runner=runner,
        available=True,
        issue=None,
    )


def _build_router(
    workspace_config: WorkspaceConfig,
) -> tuple[AutoRouter, list[RunnerEntry], list[RunnerEntry]]:
    """Build a router with all available backends.

    Returns:
        Tuple of (router, available_entries, unavailable_entries)
    """
    config_path = workspace_config.config_path()
    raw_config = _load_raw_config(config_path)
    default_engine = workspace_config.default_engine

    backends = list_backends()
    if not backends:
        raise ConfigError("No engine backends found")

    entries: list[RunnerEntry] = []
    available: list[RunnerEntry] = []
    unavailable: list[RunnerEntry] = []

    for backend in backends:
        entry = _build_runner_entry(backend, raw_config, config_path)
        entries.append(entry)
        if entry.available:
            available.append(entry)
        else:
            unavailable.append(entry)

    if not available:
        issues = [f"  - {e.engine}: {e.issue}" for e in unavailable]
        raise ConfigError("No engines available:\n" + "\n".join(issues))

    # Check if default engine is available
    default_available = any(e.engine == default_engine for e in available)
    if not default_available:
        # Fall back to first available engine
        default_engine = available[0].engine

    # Only include available entries in router
    router = AutoRouter(entries=available, default_engine=default_engine)
    return router, available, unavailable


async def _validate_bot_token(bot_token: str) -> dict | None:
    """Validate bot token by calling getMe API."""
    bot = TelegramClient(bot_token)
    try:
        return await bot.get_me()
    finally:
        await bot.close()


async def _validate_group_access(bot_token: str, group_id: int) -> dict | None:
    """Validate bot can access the group."""
    bot = TelegramClient(bot_token)
    try:
        return await bot.get_chat(group_id)
    finally:
        await bot.close()


app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Multi-model AI agent Telegram bot for multi-folder workspaces.",
)


@app.callback()
def app_main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
    final_notify: bool = typer.Option(
        True,
        "--final-notify/--no-final-notify",
        help="Send the final response as a new message (not an edit).",
    ),
    debug: bool = typer.Option(
        False,
        "--debug/--no-debug",
        help="Log engine JSONL, Telegram requests, and rendered messages.",
    ),
) -> None:
    """Pochi CLI - Multi-model AI agent workspace automation."""
    if ctx.invoked_subcommand is None:
        # Default command: run workspace
        _run_workspace(final_notify=final_notify, debug=debug)
        raise typer.Exit()


def _run_workspace(*, final_notify: bool, debug: bool) -> None:
    """Run pochi in workspace mode using the transport plugin system."""
    from .config_migrations import migrate_config_file
    from .config_store import get_config_path
    from .transport_loader import (
        TransportLoadError,
        TransportNotFoundError,
        get_configured_transports,
    )
    from .transport_runtime import TransportRuntime

    setup_logging(debug=debug)

    workspace_root = find_workspace_root()
    if workspace_root is None:
        typer.echo(
            "error: not in a workspace (no .pochi/workspace.toml found)", err=True
        )
        typer.echo("Run 'pochi init' to create a workspace here.", err=True)
        raise typer.Exit(code=1)

    # Run config migrations before loading
    config_path = get_config_path(workspace_root)
    migrations = migrate_config_file(config_path)
    if migrations:
        typer.echo(f"Applied config migrations: {', '.join(migrations)}")

    workspace_config = load_workspace_config(workspace_root)
    if workspace_config is None:
        typer.echo("error: failed to load workspace config", err=True)
        raise typer.Exit(code=1)

    # Get configured transports
    try:
        configured_transports = get_configured_transports(workspace_config)
    except TransportNotFoundError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1)
    except TransportLoadError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1)

    if not configured_transports:
        typer.echo(
            "error: no transports configured. "
            "Add [transports.telegram] or [telegram] section to workspace config.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Build router with all available engines
    try:
        router, available, unavailable = _build_router(workspace_config)
    except ConfigError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1)

    # Build shared runtime for transports
    runtime = TransportRuntime(
        router=router,
        config_path=workspace_config.config_path(),
        plugin_configs=workspace_config.plugin_configs,
        folder_aliases=tuple(workspace_config.folders.keys()),
        workspace_config=workspace_config,
        available_entries=available,
        unavailable_entries=unavailable,
    )

    # Run all configured transports
    # For now, we only support single transport at a time
    # TODO: Run multiple transports concurrently
    if len(configured_transports) > 1:
        transport_ids = [t.transport_id for t in configured_transports]
        logger.info(
            "transports.multiple_configured",
            transports=transport_ids,
            using=configured_transports[0].transport_id,
        )
        typer.echo(
            f"Note: Multiple transports configured ({', '.join(transport_ids)}). "
            f"Running {configured_transports[0].transport_id} only."
        )

    transport = configured_transports[0]
    logger.info(
        "transport.starting",
        transport=transport.transport_id,
        workspace=workspace_config.name,
    )

    try:
        transport.backend.build_and_run(
            transport_config=transport.config,
            config_path=workspace_config.config_path(),
            runtime=runtime,
            final_notify=final_notify,
            default_engine_override=None,
        )
    except KeyboardInterrupt:
        logger.info("shutdown.interrupted")
        raise typer.Exit(code=130)
    except Exception as e:
        logger.exception("transport.failed", error=str(e))
        typer.echo(f"error: transport failed: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("init", help="Initialize a workspace in current dir or [FOLDER].")
def init_command(
    folder: str = typer.Argument(
        None,
        help="Folder name to create workspace in (defaults to current directory)",
    ),
    name: str = typer.Option(
        None,
        "--name",
        "-n",
        help="Name for the workspace (defaults to folder name)",
    ),
    bot_token: str = typer.Option(
        None,
        "--bot-token",
        "-t",
        help="Telegram bot token (will prompt if not provided)",
    ),
    group_id: int = typer.Option(
        None,
        "--group-id",
        "-g",
        help="Telegram group ID (will prompt if not provided)",
    ),
) -> None:
    """Initialize a new workspace.

    If FOLDER is provided, creates a new directory with that name.
    Otherwise, initializes the workspace in the current directory.
    """
    cwd = Path.cwd()

    if folder:
        # Create in subfolder
        workspace_dir = cwd / folder
        workspace_name = name or folder

        if workspace_dir.exists():
            existing_config = load_workspace_config(workspace_dir)
            if existing_config is not None:
                typer.echo(
                    f"error: workspace already exists at {workspace_dir}", err=True
                )
                raise typer.Exit(code=1)

        workspace_dir.mkdir(parents=True, exist_ok=True)
    else:
        # Initialize in current directory
        workspace_dir = cwd
        workspace_name = name or cwd.name

        existing_config = load_workspace_config(workspace_dir)
        if existing_config is not None:
            typer.echo(f"error: workspace already exists at {workspace_dir}", err=True)
            raise typer.Exit(code=1)

    # Prompt for missing values
    if bot_token is None:
        bot_token = typer.prompt("Telegram bot token")
    if group_id is None:
        group_id_str = typer.prompt("Telegram group ID")
        try:
            group_id = int(group_id_str)
        except ValueError:
            typer.echo("error: group ID must be an integer", err=True)
            raise typer.Exit(code=1)

    # Validate bot token
    typer.echo("Validating...")
    try:
        bot_info = anyio.run(_validate_bot_token, bot_token)
    except Exception as e:
        typer.echo(f"error: failed to validate bot token: {e}", err=True)
        raise typer.Exit(code=1)

    if bot_info is None:
        typer.echo("error: invalid bot token", err=True)
        raise typer.Exit(code=1)

    bot_username = bot_info.get("username", "bot")
    typer.echo(f"✓ Connected to @{bot_username}")

    # Validate group access
    try:
        chat_info = anyio.run(_validate_group_access, bot_token, group_id)
    except Exception as e:
        typer.echo(f"error: failed to access group: {e}", err=True)
        raise typer.Exit(code=1)

    if chat_info is None:
        typer.echo(
            f"error: bot cannot access group {group_id}. "
            "Make sure the bot is added to the group.",
            err=True,
        )
        raise typer.Exit(code=1)

    chat_title = chat_info.get("title", f"group {group_id}")
    typer.echo(f"✓ Access verified for '{chat_title}'")

    # Create workspace config
    config = create_workspace(
        root=workspace_dir,
        name=workspace_name,
        telegram_group_id=group_id,
        bot_token=bot_token,
    )

    if folder:
        typer.echo(f"✓ Created workspace '{workspace_name}' at {workspace_dir}")
    else:
        typer.echo(f"✓ Initialized workspace '{workspace_name}' in {workspace_dir}")
    typer.echo(f"✓ Config saved to {config.config_path()}")
    typer.echo("")
    typer.echo("Next steps:")
    if folder:
        typer.echo(f"  cd {folder}")
    typer.echo("  pochi")


@app.command("info")
def info_command() -> None:
    """Show information about the current workspace."""
    workspace_root = find_workspace_root()
    if workspace_root is None:
        typer.echo(
            "error: not in a workspace (no .pochi/workspace.toml found)", err=True
        )
        raise typer.Exit(code=1)

    config = load_workspace_config(workspace_root)
    if config is None:
        typer.echo("error: failed to load workspace config", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Workspace: {config.name}")
    typer.echo(f"Root: {config.root}")
    typer.echo(f"Default Engine: {config.default_engine}")
    typer.echo(f"Default Transport: {config.default_transport}")
    typer.echo(f"Folders: {len(config.folders)}")

    # Show configured transports
    transport_ids = config.configured_transport_ids()
    if transport_ids:
        typer.echo(f"Transports: {', '.join(transport_ids)}")
    typer.echo("")

    if config.folders:
        typer.echo("Folders:")
        for folder_name, folder in config.folders.items():
            git_suffix = " (git)" if folder.is_git_repo(config.root) else ""
            topic_status = (
                f"topic #{folder.topic_id}" if folder.topic_id else "no topic"
            )
            if folder.pending_topic:
                topic_status = "pending topic"
            typer.echo(f"  • {folder_name}{git_suffix} ({topic_status})")
            typer.echo(f"    Path: {folder.path}")
    else:
        typer.echo("No folders configured yet.")
        typer.echo("Use /clone, /create, or /add in Telegram to add folders.")


@app.command("setup", help="Run interactive setup wizard.")
def setup_command(
    folder: str = typer.Argument(
        None,
        help="Folder to create workspace in (defaults to current directory)",
    ),
) -> None:
    """Run interactive onboarding wizard.

    This command guides you through setting up a Pochi workspace with:
    - Telegram bot token validation
    - Group ID detection (automatic or manual)
    - Configuration file creation
    """
    from .onboarding import run_onboarding_sync

    cwd = Path.cwd()
    if folder:
        workspace_dir = cwd / folder
        workspace_dir.mkdir(parents=True, exist_ok=True)
    else:
        workspace_dir = cwd

    result = run_onboarding_sync(workspace_dir)
    if result is None:
        raise typer.Exit(code=1)


@app.command("plugins", help="List discovered plugins.")
def plugins_command(
    load: bool = typer.Option(
        False,
        "--load",
        help="Load and validate all plugins, showing any errors.",
    ),
) -> None:
    """List discovered plugins without loading them.

    Use --load to load and validate all plugins, which will surface
    import errors and type mismatches.
    """
    from .plugins import (
        discover_all_plugins,
        load_plugin,
    )

    discovery = discover_all_plugins()
    has_errors = False

    for kind, result in discovery.items():
        typer.echo(f"\n{kind.upper()} PLUGINS:")

        if not result.entries and not result.errors:
            typer.echo("  (none discovered)")
            continue

        for entry in result.entries:
            dist_info = f" ({entry.distribution})" if entry.distribution else ""

            if load:
                # Load and validate
                loaded = load_plugin(entry)
                if loaded.error:
                    typer.echo(f"  ✗ {entry.id}{dist_info}")
                    typer.echo(f"    Error: {loaded.error}")
                    has_errors = True
                else:
                    typer.echo(f"  ✓ {entry.id}{dist_info}")
            else:
                # Just list entrypoint
                typer.echo(f"  • {entry.id}{dist_info}")
                typer.echo(f"    {entry.entrypoint.value}")

        # Show discovery errors
        for error in result.errors:
            typer.echo(f"  ✗ {error}")
            has_errors = True

    if has_errors:
        typer.echo("\nSome plugins failed to load. Check the errors above.")
        raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
