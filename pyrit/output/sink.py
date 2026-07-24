# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

OutputFormat = Literal["pretty", "markdown"]


class Sink(ABC):
    """
    Abstract base class for output sinks.

    A sink defines where rendered output goes (stdout, file, etc.).
    All printers write their output through a sink.
    """

    @abstractmethod
    async def write_async(self, data: str) -> None:
        """
        Write rendered output data.

        Args:
            data (str): The rendered text output to write.
        """


class StdoutSink(Sink):
    """
    Sink that prints text to stdout.

    This is the default sink used when no sink is specified.
    """

    async def write_async(self, data: str) -> None:
        """
        Write data to stdout.

        Args:
            data (str): The text to print.
        """
        encoding = sys.stdout.encoding or "utf-8"
        try:
            data.encode(encoding)
        except (LookupError, UnicodeEncodeError):
            data = data.encode(encoding, errors="replace").decode(encoding)
        sys.stdout.write(data)


class FileSink(Sink):
    """
    Sink that writes text to a file.
    """

    def __init__(self, *, path: Path, mode: str = "w") -> None:
        """
        Initialize the file sink.

        Args:
            path (Path): The file path to write to.
            mode (str): The file open mode. Defaults to "w" (write, overwrite).
                Use "a" for append mode.

        Raises:
            ValueError: If mode is not a valid text write mode.
        """
        if mode not in ("w", "a"):
            raise ValueError(f"mode must be 'w' or 'a', got '{mode}'")
        self._path = path
        self._mode = mode
        self._lock = asyncio.Lock()

    async def write_async(self, data: str) -> None:
        """
        Write data to a file.

        Args:
            data (str): The text to write.
        """
        async with self._lock:
            await asyncio.to_thread(self._write_sync, data)

    def _write_sync(self, data: str) -> None:
        """
        Write data to the file synchronously.

        Args:
            data (str): The text to write.
        """
        with open(self._path, self._mode, encoding="utf-8") as f:
            f.write(data)


class IPythonMarkdownSink(Sink):
    """
    Sink that renders markdown via IPython's ``display(Markdown(...))``.

    Falls back to ``print()`` if IPython is not available (e.g., outside
    a Jupyter notebook).
    """

    async def write_async(self, data: str) -> None:
        """
        Display data as rendered markdown in IPython, or print to stdout.

        Args:
            data (str): The markdown text to display.
        """
        try:
            from IPython.display import Markdown, display

            display(Markdown(data))
        except (ImportError, NameError):
            print(data, end="")


def get_default_sink(default: type[Sink] | None = None) -> Sink:
    """
    Return the appropriate default sink for the current environment.

    When ``default`` is None, auto-detects: uses ``IPythonMarkdownSink``
    inside Jupyter/IPython notebooks, otherwise ``StdoutSink``.

    Args:
        default (type[Sink] | None): Sink class to instantiate.
            None means auto-detect based on environment.

    Returns:
        Sink: The default sink instance.
    """
    if default is not None:
        return default()
    from pyrit.common.notebook_utils import is_in_ipython_session

    if is_in_ipython_session():
        return IPythonMarkdownSink()
    return StdoutSink()
