# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrit.output.sink import FileSink, IPythonMarkdownSink, Sink, StdoutSink, get_default_sink


def test_sink_is_abstract():
    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        Sink()  # type: ignore[abstract]


async def test_stdout_sink_writes_to_stdout(capsys):
    sink = StdoutSink()
    await sink.write_async("hello world")
    captured = capsys.readouterr()
    assert captured.out == "hello world"


async def test_stdout_sink_no_trailing_newline(capsys):
    sink = StdoutSink()
    await sink.write_async("line1")
    await sink.write_async("line2")
    captured = capsys.readouterr()
    assert captured.out == "line1line2"


async def test_stdout_sink_replaces_unsupported_unicode():
    stdout = MagicMock()
    stdout.encoding = "cp1252"
    sink = StdoutSink()

    with patch("pyrit.output.sink.sys.stdout", stdout):
        await sink.write_async("📊─")

    stdout.write.assert_called_once_with("??")


async def test_file_sink_writes_to_file():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        path = Path(f.name)

    try:
        sink = FileSink(path=path, mode="w")
        await sink.write_async("hello file")
        assert path.read_text(encoding="utf-8") == "hello file"
    finally:
        path.unlink(missing_ok=True)


async def test_file_sink_append_mode():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        path = Path(f.name)

    try:
        sink = FileSink(path=path, mode="w")
        await sink.write_async("first")

        append_sink = FileSink(path=path, mode="a")
        await append_sink.write_async(" second")

        assert path.read_text(encoding="utf-8") == "first second"
    finally:
        path.unlink(missing_ok=True)


def test_file_sink_rejects_invalid_mode():
    with pytest.raises(ValueError, match="mode must be 'w' or 'a'"):
        FileSink(path=Path("test.txt"), mode="wb")


# --- IPythonMarkdownSink tests ---


async def test_ipython_markdown_sink_fallback_to_print(capsys):
    """When IPython is not available, falls back to print()."""
    sink = IPythonMarkdownSink()
    with patch.dict(sys.modules, {"IPython": None, "IPython.display": None}):
        await sink.write_async("# Hello")
    captured = capsys.readouterr()
    assert "# Hello" in captured.out


async def test_ipython_markdown_sink_with_ipython_available():
    """When IPython is available, uses display(Markdown(...))."""
    mock_display = MagicMock()
    mock_markdown_cls = MagicMock()

    ipython_display_module = MagicMock()
    ipython_display_module.display = mock_display
    ipython_display_module.Markdown = mock_markdown_cls

    sink = IPythonMarkdownSink()
    with patch.dict(sys.modules, {"IPython": MagicMock(), "IPython.display": ipython_display_module}):
        await sink.write_async("**bold**")

    mock_markdown_cls.assert_called_once_with("**bold**")
    mock_display.assert_called_once()


# --- get_default_sink tests ---


def test_get_default_sink_no_default_returns_stdout_outside_notebook():
    result = get_default_sink()
    assert isinstance(result, StdoutSink)


def test_get_default_sink_explicit_ipython():
    result = get_default_sink(IPythonMarkdownSink)
    assert isinstance(result, IPythonMarkdownSink)


def test_get_default_sink_explicit_stdout():
    result = get_default_sink(StdoutSink)
    assert isinstance(result, StdoutSink)
