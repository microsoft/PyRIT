# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.printer.helpers import (
    print_attack_result_async,
    print_scenario_result_async,
    print_scorer_async,
)
from pyrit.printer.sink import FileSink, OutputFormat, StdoutSink, resolve_sink


# --- _resolve_sink tests ---


def test_resolve_sink_none_returns_stdout():
    sink = resolve_sink(None)
    assert isinstance(sink, StdoutSink)


def test_resolve_sink_path_returns_file_sink():
    sink = resolve_sink(Path("output.txt"))
    assert isinstance(sink, FileSink)


def test_resolve_sink_str_returns_file_sink():
    sink = resolve_sink("output.txt")
    assert isinstance(sink, FileSink)


def test_resolve_sink_sink_instance_passthrough():
    original = StdoutSink()
    sink = resolve_sink(original)
    assert sink is original


# --- print_attack_result_async tests ---


@patch("pyrit.printer.attack_result.pretty.PrettyAttackResultMemoryPrinter")
async def test_print_attack_result_async_pretty_default(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()

    await print_attack_result_async(result)

    mock_cls.assert_called_once()
    mock_printer.write_async.assert_called_once()


@patch("pyrit.printer.attack_result.markdown.MarkdownAttackResultMemoryPrinter")
async def test_print_attack_result_async_markdown(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()

    await print_attack_result_async(result, format="markdown")

    mock_cls.assert_called_once()
    mock_printer.write_async.assert_called_once()


@patch("pyrit.printer.attack_result.pretty.PrettyAttackResultMemoryPrinter")
async def test_print_attack_result_async_to_file(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()

    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        path = Path(f.name)

    try:
        await print_attack_result_async(result, sink=path)
        call_kwargs = mock_cls.call_args[1]
        assert isinstance(call_kwargs["sink"], FileSink)
    finally:
        path.unlink(missing_ok=True)


# --- print_scenario_result_async tests ---


@patch("pyrit.printer.scenario_result.pretty.PrettyScenarioResultMemoryPrinter")
async def test_print_scenario_result_async_pretty(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()

    await print_scenario_result_async(result)

    mock_cls.assert_called_once()
    mock_printer.write_async.assert_called_once_with(result)


async def test_print_scenario_result_async_unsupported_format():
    with pytest.raises(ValueError, match="Unsupported format"):
        await print_scenario_result_async(MagicMock(), format="markdown")


# --- print_scorer_async tests ---


@patch("pyrit.printer.scorer.pretty.PrettyScorerMemoryPrinter")
async def test_print_scorer_async_pretty(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    scorer_id = MagicMock()

    await print_scorer_async(scorer_identifier=scorer_id)

    mock_cls.assert_called_once()
    mock_printer.write_async.assert_called_once_with(scorer_identifier=scorer_id, harm_category=None)


@patch("pyrit.printer.scorer.pretty.PrettyScorerMemoryPrinter")
async def test_print_scorer_async_with_harm_category(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    scorer_id = MagicMock()

    await print_scorer_async(scorer_identifier=scorer_id, harm_category="hate_speech")

    mock_printer.write_async.assert_called_once_with(scorer_identifier=scorer_id, harm_category="hate_speech")


async def test_print_scorer_async_unsupported_format():
    with pytest.raises(ValueError, match="Unsupported format"):
        await print_scorer_async(scorer_identifier=MagicMock(), format="markdown")
