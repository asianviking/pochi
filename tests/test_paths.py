from __future__ import annotations

from pathlib import Path

from pochi.utils.paths import relativize_command, relativize_path


def test_relativize_command_rewrites_cwd_paths(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    command = f'find {base}/tests -type f -name "*.py" | head -20'
    expected = 'find tests -type f -name "*.py" | head -20'
    assert relativize_command(command, base_dir=base) == expected


def test_relativize_command_rewrites_equals_paths(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    command = f'rg -n --files -g "*.py" --path={base}/src'
    expected = 'rg -n --files -g "*.py" --path=src'
    assert relativize_command(command, base_dir=base) == expected


def test_relativize_path_ignores_sibling_prefix(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    value = str(tmp_path / "repo2" / "file.txt")
    assert relativize_path(value, base_dir=base) == value


def test_relativize_path_inside_base(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    value = str(base / "src" / "app.py")
    assert relativize_path(value, base_dir=base) == "src/app.py"


def test_relativize_path_empty_value() -> None:
    """Test relativize_path with empty string returns empty."""
    assert relativize_path("") == ""


def test_relativize_path_same_as_base(tmp_path: Path) -> None:
    """Test relativize_path when value equals base returns '.'"""
    base = tmp_path / "repo"
    base.mkdir()
    value = str(base)
    assert relativize_path(value, base_dir=base) == "."


def test_relativize_path_returns_dot_for_empty_suffix(tmp_path: Path) -> None:
    """Test relativize_path returns '.' when suffix is empty after prefix strip."""
    base = tmp_path / "repo"
    base.mkdir()
    # Value is base + separator - edge case
    value = f"{base}/"
    assert relativize_path(value, base_dir=base) == "."


def test_relativize_command_no_base_dir() -> None:
    """Test relativize_command uses cwd when no base_dir provided."""
    result = relativize_command("echo hello")
    assert result == "echo hello"


def test_relativize_path_no_base_dir(tmp_path: Path) -> None:
    """Test relativize_path uses cwd when no base_dir provided."""
    import os

    # Save cwd
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        # Test with path outside cwd
        result = relativize_path("/some/other/path")
        assert result == "/some/other/path"
    finally:
        os.chdir(original_cwd)
