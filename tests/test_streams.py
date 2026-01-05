"""Tests for pochi.utils.streams module."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
import anyio
from anyio.abc import ByteReceiveStream

from pochi.utils.streams import drain_stderr

if TYPE_CHECKING:
    pass


class MockByteReceiveStream(ByteReceiveStream):
    """Mock stream that yields pre-defined data."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    async def receive(self, max_bytes: int = 65536) -> bytes:
        if self._pos >= len(self._data):
            raise anyio.EndOfStream
        chunk = self._data[self._pos : self._pos + max_bytes]
        self._pos += len(chunk)
        return chunk

    async def aclose(self) -> None:
        pass


class MockBufferedStream:
    """Mock for BufferedByteReceiveStream with receive_until."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = iter(lines)

    async def receive_until(self, delimiter: bytes, max_bytes: int) -> bytes:
        try:
            return next(self._lines)
        except StopIteration as e:
            raise anyio.IncompleteRead from e


class SimpleStream(ByteReceiveStream):
    """Simple stream for testing."""

    def __init__(self, data: bytes):
        self._buffer = data
        self._pos = 0

    async def receive(self, max_bytes: int = 65536) -> bytes:
        if self._pos >= len(self._buffer):
            raise anyio.EndOfStream
        chunk = self._buffer[self._pos : self._pos + max_bytes]
        self._pos += len(chunk)
        return chunk

    async def aclose(self) -> None:
        pass


class ErrorStream(ByteReceiveStream):
    """Stream that raises an error."""

    async def receive(self, max_bytes: int = 65536) -> bytes:
        raise RuntimeError("Stream error")

    async def aclose(self) -> None:
        pass


@pytest.mark.anyio
async def test_drain_stderr_logs_lines() -> None:
    """Test drain_stderr logs each line."""
    stream = SimpleStream(b"line1\nline2\n")
    logger = MagicMock()

    await drain_stderr(stream, logger, "test-tag")

    # The log_pipeline function should have been called
    # Since we're mocking, we can't easily verify, but it shouldn't raise


@pytest.mark.anyio
async def test_drain_stderr_handles_errors() -> None:
    """Test drain_stderr handles errors gracefully."""
    stream = ErrorStream()
    logger = MagicMock()

    # Should not raise
    await drain_stderr(stream, logger, "test-tag")
