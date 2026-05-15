# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import ABC

from pyrit.printer.sink import Sink, StdoutSink


class PrinterBase(ABC):
    """
    Abstract base class for all printers.

    Provides a sink for output routing. Subclasses write their rendered
    output through the sink via ``_write_async``.
    """

    def __init__(self, *, sink: Sink | None = None) -> None:
        """
        Initialize the printer base.

        Args:
            sink (Sink | None): The output sink. Defaults to StdoutSink() if not provided.
        """
        self._sink = sink or StdoutSink()

    async def _write_async(self, data: bytes) -> None:
        """
        Write data through the configured sink.

        Args:
            data (bytes): The rendered output to write.
        """
        await self._sink.write_async(data)
