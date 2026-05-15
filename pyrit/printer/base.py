# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import ABC, abstractmethod

from pyrit.printer.sink import Sink, StdoutSink


class PrinterBase(ABC):
    """
    Abstract base class for all printers.

    Provides a sink for output routing. Subclasses must implement
    ``write_async`` as their public entry point, and use ``_write_async``
    to send rendered text to the sink.
    """

    def __init__(self, *, sink: Sink | None = None) -> None:
        """
        Initialize the printer base.

        Args:
            sink (Sink | None): The output sink. Defaults to StdoutSink() if not provided.
        """
        self._sink = sink or StdoutSink()

    @abstractmethod
    async def write_async(self, *args, **kwargs) -> None:
        """
        Render and write output to the configured sink.

        Subclasses define the specific signature (e.g., scorer_identifier,
        result, etc.).
        """

    async def _write_async(self, data: str) -> None:
        """
        Write data through the configured sink.

        Args:
            data (str): The rendered text output to write.
        """
        await self._sink.write_async(data)
