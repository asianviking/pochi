# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.3] - 2026-01-07

### Added

- Transport-agnostic abstractions for multi-transport support (Phase 1 of #9)
  - `Transport` protocol for abstracting message delivery
  - `Presenter` protocol for rendering progress and final messages
  - `ProgressTracker` and state classes for event reduction
  - `MarkdownFormatter` and `MarkdownPresenter` for markdown rendering
  - `CommandResult` and `CommandRegistry` for transport-agnostic commands
  - `ChannelRouter` and `RouteResult` for message routing
  - `WorkspaceManager` for folder operations
  - `runner_bridge.py` for transport-agnostic runner execution

### Changed

- Refactored Telegram code into `telegram/` package with clear separation:
  - `telegram/client.py`: TelegramClient and TelegramOutbox
  - `telegram/transport.py`: TelegramTransport implementing Transport protocol
  - `telegram/presenter.py`: TelegramPresenter for Telegram-specific rendering
  - `telegram/bridge.py`: Polling and update handling
- Extended config with `TelegramConfig` and multi-transport channel support
- Backwards compatibility maintained via re-exports in `telegram.py`

## [0.1.2] - 2026-01-05

### Added

- Support for Codex, OpenCode, and Pi runners with auto-discovery
- Unit tests for Codex, OpenCode, and Pi runners
- Release checklist in CLAUDE.md

## [0.1.1] - 2026-01-05

### Fixed

- Consolidated duplicate README files into single detailed readme.md

## [0.1.0] - 2026-01-05

Initial release.

### Added

- **Workspace-first design**: One Telegram group maps to one workspace, each folder gets its own topic
- **Multi-engine support**: Extensible backend system with auto-discovery (ships with Claude runner)
- **Folder management commands**: `/clone`, `/create`, `/add`, `/remove`, `/list`, `/status`
- **Resume tokens**: Stateless conversation continuity - continue in Telegram or pick up in terminal with `claude --resume <token>`
- **Ralph Wiggum loops**: Iterative prompting where Claude reviews its own work until satisfied (`/ralph` command)
- **Real-time progress**: Streaming updates showing commands, tools, file changes, and elapsed time
- **Per-topic job queuing**: Each folder processes messages sequentially to prevent race conditions
- **CLI commands**: `pochi init`, `pochi run`, `pochi info`, `pochi --version`
- **TOML configuration**: Workspace config at `.pochi/workspace.toml` with per-engine settings

### Requirements

- Python 3.14+
- Claude Code CLI (`npm install -g @anthropic-ai/claude-code`)
- Telegram bot token and group with topics enabled
