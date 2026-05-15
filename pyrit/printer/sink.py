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
    async def write_async(self, data: bytes) -> None:
        """
        Write rendered output data.

        Args:
            data (bytes): The rendered output to write.
        """


class StdoutSink(Sink):
    """
    Sink that decodes bytes to str and prints to stdout.

    This is the default sink used when no sink is specified.
    """

    def __init__(self, *, encoding: str = "utf-8") -> None:
        """
        Initialize the stdout sink.

        Args:
            encoding (str): Character encoding for decoding bytes. Defaults to "utf-8".
        """
        self._encoding = encoding

    async def write_async(self, data: bytes) -> None:
        """
        Write data to stdout.

        Args:
            data (bytes): The data to print, decoded using the configured encoding.
        """
        print(data.decode(self._encoding), end="")


class FileSink(Sink):
    """
    Sink that writes bytes to a file.
    """

    def __init__(self, *, path: Path, mode: str = "wb") -> None:
        """
        Initialize the file sink.

        Args:
            path (Path): The file path to write to.
            mode (str): The file open mode. Defaults to "wb" (write binary, overwrite).
                Use "ab" for append mode.

        Raises:
            ValueError: If mode is not a valid binary write mode.
        """
        if mode not in ("wb", "ab"):
            raise ValueError(f"mode must be 'wb' or 'ab', got '{mode}'")
        self._path = path
        self._mode = mode

    async def write_async(self, data: bytes) -> None:
        """
        Write data to a file.

        Args:
            data (bytes): The data to write.
        """
        with open(self._path, self._mode) as f:
            f.write(data)
