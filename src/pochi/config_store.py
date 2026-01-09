"""Raw TOML configuration I/O utilities.

Separates file I/O from validation logic, enabling config migrations
to work with raw data before pydantic validation.
"""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path
from typing import Any

import tomlkit

WORKSPACE_CONFIG_DIR = ".pochi"
WORKSPACE_CONFIG_FILE = "workspace.toml"


def get_config_path(workspace_root: Path) -> Path:
    """Get the path to the workspace config file."""
    return workspace_root / WORKSPACE_CONFIG_DIR / WORKSPACE_CONFIG_FILE


def read_raw_toml(path: Path) -> dict[str, Any]:
    """Read raw TOML data from a file.

    Args:
        path: Path to the TOML file

    Returns:
        Parsed TOML data as a dictionary

    Raises:
        FileNotFoundError: If the file does not exist
        tomllib.TOMLDecodeError: If the file is not valid TOML
    """
    with open(path, "rb") as f:
        return tomllib.load(f)


def write_raw_toml(data: dict[str, Any], path: Path) -> None:
    """Write raw TOML data to a file.

    Uses tomlkit to preserve formatting where possible.

    Args:
        data: Dictionary to write as TOML
        path: Path to write to
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    content = tomlkit.dumps(data)
    path.write_text(content)


def backup_config(path: Path) -> Path | None:
    """Create a backup of the config file.

    Args:
        path: Path to the config file

    Returns:
        Path to the backup file, or None if no backup was needed
    """
    if not path.exists():
        return None

    backup_path = path.with_suffix(".toml.bak")
    shutil.copy2(path, backup_path)
    return backup_path
