# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import ABC, abstractmethod
from typing import Any

from pyrit.output.sink import Sink, StdoutSink


class PrinterBase(ABC):
    """
    Abstract base class for all printers.

    Subclasses implement ``render_async`` to produce formatted text.
    ``write_async`` is concrete: it calls ``render_async`` then routes
    the result through the configured sink.
    """

    def __init__(self, *, sink: Sink | None = None) -> None:
        """
        Initialize the printer base.

        Args:
            sink (Sink | None): The output sink. Defaults to StdoutSink() if not provided.
        """
        self._sink = sink or StdoutSink()

    @abstractmethod
    async def render_async(self, *args: Any, **kwargs: Any) -> str:
        """
        Render output and return it as a string.

        Subclasses define the specific signature (e.g., scorer_identifier,
        result, messages, etc.).
        """

    async def write_async(self, *args: Any, **kwargs: Any) -> None:
        """
        Render output and write it to the configured sink.

        Calls ``render_async`` with all arguments, then writes the result
        through the sink. Subclasses should not override this method.
        """
        content = await self.render_async(*args, **kwargs)
        await self._write_async(content)

    async def _write_async(self, data: str) -> None:
        """
        Write data through the configured sink.

        Args:
            data (str): The rendered text output to write.
        """
        await self._sink.write_async(data)
