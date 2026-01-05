# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
