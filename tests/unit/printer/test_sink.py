# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import tempfile
from pathlib import Path

import pytest

from pyrit.printer.sink import FileSink, Sink, StdoutSink


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
