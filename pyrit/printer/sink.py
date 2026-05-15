# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import ABC, abstractmethod
from pathlib import Path


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
        print(data, end="")


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

    async def write_async(self, data: str) -> None:
        """
        Write data to a file.

        Args:
            data (str): The text to write.
        """
        with open(self._path, self._mode, encoding="utf-8") as f:
            f.write(data)
