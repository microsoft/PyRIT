# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.printer.base import PrinterBase
from pyrit.printer.sink import StdoutSink


def test_printer_base_has_no_abstract_methods():
    # PrinterBase is abstract via ABC but has no abstract methods of its own.
    # Subclasses add their own abstract methods for data fetching.
    class ConcretePrinter(PrinterBase):
        pass

    printer = ConcretePrinter()
    assert isinstance(printer, PrinterBase)


def test_printer_base_defaults_to_stdout_sink():

    class ConcretePrinter(PrinterBase):
        pass

    printer = ConcretePrinter()
    assert isinstance(printer._sink, StdoutSink)


def test_printer_base_accepts_custom_sink():
    from pyrit.printer.sink import FileSink
    from pathlib import Path

    class ConcretePrinter(PrinterBase):
        pass

    sink = FileSink(path=Path("test.txt"))
    printer = ConcretePrinter(sink=sink)
    assert printer._sink is sink


async def test_printer_base_write_async_delegates_to_sink(capsys):

    class ConcretePrinter(PrinterBase):
        pass

    printer = ConcretePrinter()
    await printer._write_async(b"test output")
    captured = capsys.readouterr()
    assert captured.out == "test output"
