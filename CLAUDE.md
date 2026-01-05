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

## Feature Planning Workflow

When planning a new feature, follow this workflow:

### 1. Create a GitHub Issue
- Before writing any code, create a GitHub issue for the feature
- Use `gh issue create` with a clear title and initial description
- Label appropriately (e.g., `enhancement`, `feature`)

### 2. Develop the Plan in the Issue
- Write out the proposed implementation plan in the issue body
- Include: goals, approach, affected files, and potential concerns
- Use AskUserQuestion to clarify requirements and resolve ambiguities
- Update the issue as questions are answered and the plan evolves

### 3. Iterate Until Clear
- Continue asking questions until there are no remaining unknowns
- Each clarification should be reflected in the updated issue
- The issue should serve as a complete specification when done

### 4. Get Approval Before Implementation
- Only start coding once the plan in the issue is solid
- The issue becomes the reference document during implementation
- Link commits and PRs back to the issue

This ensures features are well-thought-out, documented, and aligned with user expectations before any code is written.

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

## Multi-Platform Support

Pochi supports both Telegram and Discord as chat platforms, with a unified config format.

### Platform Configuration

```toml
# .pochi/workspace.toml
[workspace]
name = "my-workspace"
default_engine = "claude"

# Telegram configuration
[telegram]
bot_token = "123456:ABC..."
group_id = -123456789
admin_user = 123456789
allowed_users = [987654321]

# Discord configuration (optional)
[discord]
bot_token = "your-discord-bot-token"
guild_id = 123456789012345678
category_id = 234567890123456789
admin_user = 345678901234567890
allowed_users = [456789012345678901]
```

### Platform-Specific Behavior

**Telegram:**
- Uses forum topics for workspace folders
- Messages create/continue thread in topic
- Commands: `/clone`, `/create`, `/add`, `/list`, `/users`, etc.

**Discord:**
- Uses channels within a category for workspace folders
- Messages in channels auto-create threads for conversations
- Native slash commands with autocomplete: `/list`, `/status`, `/engine`, `/users`, etc.
- Thread-to-session mapping for resume tokens

### Key Modules

```
src/pochi/
├── chat.py                      # Platform-agnostic abstractions
├── telegram.py                  # Telegram client + TelegramProvider
├── discord.py                   # Discord client (discord.py wrapper)
├── workspace/
│   ├── bridge.py                # Telegram workspace bridge
│   ├── discord_bridge.py        # Discord workspace bridge
│   ├── discord_commands.py      # Discord slash command registration
│   └── discord_router.py        # Discord channel-to-folder routing
```

### Chat Abstraction

The `chat.py` module provides platform-agnostic types:
- `ChatProvider` protocol for platform implementations
- `ChatUpdate` for normalized incoming messages
- `Destination` for where to send messages
- `MessageRef` for referencing sent messages

## User Authentication

Pochi includes user-level authentication to restrict bot access to authorized users only.

### Configuration

Each platform has its own user authentication:

```toml
# Telegram users
[telegram]
admin_user = 123456789           # Telegram user ID of admin
allowed_users = [987654321]      # Guest user IDs

# Discord users (separate from Telegram)
[discord]
admin_user = 345678901234567890  # Discord user ID of admin
allowed_users = [456789012345678901]
```

### User Roles

| Role | Can use bot | `/adduser` | `/removeuser` |
|------|-------------|------------|---------------|
| **Admin** | Yes | Yes | Yes |
| **Guest** | Yes | No | No |
| **Unauthorized** | No | No | No |

### Bootstrap Flow

1. Bot starts with no `admin_user` set
2. First person to message the bot becomes admin automatically
3. Admin uses `/adduser` to invite guests
4. To change admin: manually edit `workspace.toml`

### User Commands

- `/users` - List admin and all guests (admin + guests can use)
- `/adduser <id>` or `/adduser @user` - Add a guest user (admin only)
- `/removeuser <id>` or `/removeuser @user` - Remove a guest user (admin only)

### Security Notes

- Unauthorized users are silently ignored (no reply)
- Users are identified by platform user ID (not @username)
- Admin cannot be changed via commands (requires config edit)
- Each platform has independent user lists

## Release Checklist

Follow these steps when preparing a release:

### 1. Version Update
- Update version in `pyproject.toml`
- Update version in `src/pochi/__init__.py`

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
