# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.printer.base import PrinterBase
from pyrit.printer.sink import StdoutSink


def test_printer_base_is_abstract():
    class IncompletePrinter(PrinterBase):
        pass

    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        IncompletePrinter()  # type: ignore[abstract]


def test_printer_base_defaults_to_stdout_sink():

    class ConcretePrinter(PrinterBase):
        async def write_async(self) -> None:
            pass

    printer = ConcretePrinter()
    assert isinstance(printer._sink, StdoutSink)


def test_printer_base_accepts_custom_sink():
    from pathlib import Path

    from pyrit.printer.sink import FileSink

    class ConcretePrinter(PrinterBase):
        async def write_async(self) -> None:
            pass

    sink = FileSink(path=Path("test.txt"))
    printer = ConcretePrinter(sink=sink)
    assert printer._sink is sink


async def test_printer_base_write_async_delegates_to_sink(capsys):

    class ConcretePrinter(PrinterBase):
        async def write_async(self) -> None:
            await self._write_async("test output")

    printer = ConcretePrinter()
    await printer.write_async()
    captured = capsys.readouterr()
    assert captured.out == "test output"
