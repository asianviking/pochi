# CLAUDE.md

This file provides context for Claude Code when working on this codebase.

## Project Overview

Pochi is a multi-model AI agent Telegram bot for multi-folder workspaces. It bridges AI coding agents (Claude Code, Codex, OpenCode, Pi, etc.) to Telegram, enabling developers to interact with agents from their phone or any Telegram client while maintaining full context and resume capability.

## Architecture

### Workspace-First Design

Pochi uses a workspace model where:
- One Telegram group (with topics enabled) maps to one workspace
- Each folder gets its own Telegram topic (can be git repos or plain directories)
- The "General" topic handles workspace management commands
- Config lives in `.pochi/workspace.toml`

### Key Modules

```
src/pochi/
├── cli.py              # Typer CLI: init, run, info commands
├── bridge.py           # Core Telegram event loop (single-folder mode)
├── backends.py         # EngineBackend dataclass for runner factories
├── backends_helpers.py # Backend setup helpers
├── config.py           # ConfigError and core config utilities
├── engines.py          # Engine auto-discovery registry (multi-model)
├── events.py           # Pochi event model (Started, Action, Completed)
├── logging.py          # Structured logging via structlog
├── model.py            # ResumeToken, Action dataclasses
├── render.py           # Progress/final message rendering
├── router.py           # AutoRouter for resume token extraction
├── runner.py           # Base runner protocol and helpers
├── scheduler.py        # Per-topic job queue serialization
├── telegram.py         # TelegramClient HTTP wrapper
├── runners/
│   ├── claude.py       # Claude CLI runner (stream-json parsing)
│   └── mock.py         # Mock runner for testing
├── schemas/
│   └── claude.py       # msgspec schemas for Claude stream-json
├── utils/
│   ├── paths.py        # Path utilities
│   ├── streams.py      # Async stream helpers
│   └── subprocess.py   # Subprocess utilities
└── workspace/
    ├── bridge.py       # Workspace-aware Telegram event loop
    ├── commands.py     # Slash command handlers (/clone, /create, /add, etc.)
    ├── config.py       # WorkspaceConfig, FolderConfig dataclasses
    ├── manager.py      # Folder operations (add, remove, create topics)
    ├── orchestrator.py # Coordinates workspace message handling
    ├── ralph.py        # Ralph Wiggum iterative loop implementation
    └── router.py       # Routes messages to correct folder by topic
```

### Event Model

Pochi uses a normalized event model consumed by renderers:

- `Started`: Run began, contains resume token
- `Action`: Tool use, command, file change (has phases: started/updated/completed)
- `Completed`: Run finished, contains answer and final resume token

The Claude runner translates Claude's `stream-json` output into these events.

### Resume Tokens

Resume tokens enable stateless conversation continuation:
- Format: `` `claude --resume <token>` ``
- Topic-scoped format: `topic:123:token` or `general:token`
- Extracted from message text or reply-to message
- Can be used in terminal: `claude --resume <token>`

## Development

### Setup

```sh
uv sync
uv run pytest
```

### Running

```sh
# In a workspace directory with .pochi/workspace.toml
uv run pochi

# With debug logging
uv run pochi --debug
```

### Testing

```sh
uv run pytest                    # all tests
uv run pytest tests/test_cli.py  # specific file
uv run pytest -k "test_name"     # specific test
```

### Code Style

- Python 3.14+ required
- Uses `ruff` for linting/formatting
- Type hints throughout
- Async/await with `anyio` (not raw asyncio)
- Structured logging via `structlog`

## Key Patterns

### Runner Protocol

Runners implement:
```python
class Runner(Protocol):
    engine: str
    async def run(prompt: str, resume: ResumeToken | None) -> AsyncIterator[PochiEvent]
    def format_resume(token: ResumeToken) -> str
    def extract_resume(text: str) -> ResumeToken | None
```

### Per-Thread Serialization

- Each topic/thread has at most one active run
- Jobs queue per-topic and execute sequentially
- Prevents race conditions on the same conversation

### Progress Throttling

- Progress messages update at most every 2 seconds
- Edits skip if content unchanged
- Final message preserves resume token even when truncating

## Common Tasks

### Adding a new workspace command

1. Add handler in `src/pochi/workspace/commands.py`
2. Add to `handlers` dict in `handle_slash_command()`
3. Update help text in `_handle_help()`

### Modifying Claude runner behavior

1. Edit `src/pochi/runners/claude.py`
2. Stream-json schema in `src/pochi/schemas/claude.py`
3. Event translation in `translate_claude_event()`

### Changing config schema

1. Update dataclasses in `src/pochi/workspace/config.py`
2. Update `_parse_workspace_config()` for loading
3. Update `save_workspace_config()` for saving

### Adding a new engine backend

1. Create `src/pochi/runners/{engine}.py` with runner implementation
2. Export `BACKEND = EngineBackend(id="engine", build_runner=build_runner, ...)`
3. Add schema in `src/pochi/schemas/{engine}.py` if needed
4. The engine is auto-discovered from the `runners/` module

Example backend export:
```python
BACKEND = EngineBackend(
    id="myengine",
    build_runner=build_runner,
    cli_cmd="myengine",  # Optional: CLI command to check availability
    install_cmd="npm install -g myengine",  # Optional: Install instructions
)
```

## Multi-Engine Support

Pochi supports multiple AI agent backends via auto-discovery:

### Configuration

```toml
# .pochi/workspace.toml
[workspace]
name = "my-workspace"
default_engine = "claude"  # Optional, defaults to "claude"

# Per-engine configuration sections
[claude]
model = "opus"
allowed_tools = ["Bash", "Read", "Edit", "Write"]

[codex]
profile = "default"
```

### Engine Commands

- `/engine` - Show current default engine and available engines
- `/engine <name>` - Set default engine for new conversations

### Resume Tokens

Resume tokens are engine-specific. Each engine has its own format:
- Claude: `` `claude --resume <token>` ``
- Other engines follow similar patterns

The AutoRouter automatically routes messages to the correct engine based on resume tokens.

## Release Checklist

Follow these steps when preparing a release:

### 1. Version Update
- Update version in `pyproject.toml`

### 2. Code Quality
- Run `uv run ruff check .` - fix any linting issues
- Run `uv run ruff format .` - ensure consistent formatting
- Run `uv run pytest` - ensure all tests pass

### 3. Dependencies
- Review `uv.lock` for any pending changes
- Ensure `pyproject.toml` dependencies are correct and pinned appropriately

### 4. Documentation
- Update `CHANGELOG.md` with new features/fixes for this release
- Update `README.md` with any new features/changes
- Ensure `CLAUDE.md` reflects current architecture

### 5. Git Hygiene
- Commit any pending changes
- Ensure main branch is clean
- Tag the release (e.g., `v0.1.2`) - this triggers the release workflow
