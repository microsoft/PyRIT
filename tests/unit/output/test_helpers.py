# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.output.helpers import (
    output_attack_async,
    output_conversation_async,
    output_scenario_async,
    output_score_async,
    output_scorer_async,
)
from pyrit.output.sink import IPythonMarkdownSink, StdoutSink, get_default_sink

# --- get_default_sink tests ---


def test_get_default_sink_no_default_returns_stdout_outside_notebook():
    sink = get_default_sink()
    assert isinstance(sink, StdoutSink)


def test_get_default_sink_explicit_default():
    sink = get_default_sink(IPythonMarkdownSink)
    assert isinstance(sink, IPythonMarkdownSink)


def test_get_default_sink_explicit_stdout():
    sink = get_default_sink(StdoutSink)
    assert isinstance(sink, StdoutSink)


@patch("pyrit.common.notebook_utils.is_in_ipython_session", return_value=True)
def test_get_default_sink_auto_detects_notebook(_mock):
    sink = get_default_sink()
    assert isinstance(sink, IPythonMarkdownSink)


# --- output_attack_async tests ---


@patch("pyrit.output.helpers.PrettyAttackResultMemoryPrinter")
async def test_output_attack_async_pretty_default(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()

    await output_attack_async(result)

    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    assert isinstance(call_kwargs["sink"], StdoutSink)
    mock_printer.write_async.assert_called_once()


@patch("pyrit.output.helpers.MarkdownAttackResultMemoryPrinter")
async def test_output_attack_async_markdown_auto_detects_sink(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()

    await output_attack_async(result, format="markdown")

    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    # Outside a notebook, auto-detect falls back to StdoutSink
    assert isinstance(call_kwargs["sink"], StdoutSink)
    mock_printer.write_async.assert_called_once()


@patch("pyrit.output.helpers.PrettyAttackResultMemoryPrinter")
async def test_output_attack_async_explicit_sink(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()
    custom_sink = StdoutSink()

    await output_attack_async(result, sink=custom_sink)

    call_kwargs = mock_cls.call_args[1]
    assert call_kwargs["sink"] is custom_sink


# --- output_scenario_async tests ---


@patch("pyrit.output.helpers.PrettyScenarioResultMemoryPrinter")
async def test_output_scenario_async_pretty(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()

    await output_scenario_async(result)

    mock_cls.assert_called_once()
    mock_printer.write_async.assert_called_once_with(result)


@patch("pyrit.output.helpers.PrettyScenarioResultMemoryPrinter")
async def test_output_scenario_async_forwards_sort_groups_by_success_rate(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()

    await output_scenario_async(result, sort_groups_by_success_rate=True)

    assert mock_cls.call_args.kwargs["sort_groups_by_success_rate"] is True
    mock_printer.write_async.assert_called_once_with(result)


async def test_output_scenario_async_unsupported_format():
    with pytest.raises(ValueError, match="Unsupported format"):
        await output_scenario_async(MagicMock(), format="markdown")


# --- output_scorer_async tests ---


@patch("pyrit.output.helpers.PrettyScorerMemoryPrinter")
async def test_output_scorer_async_pretty(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    scorer_id = MagicMock()

    await output_scorer_async(scorer_identifier=scorer_id)

    mock_cls.assert_called_once()
    mock_printer.write_async.assert_called_once_with(scorer_identifier=scorer_id, harm_category=None)


@patch("pyrit.output.helpers.PrettyScorerMemoryPrinter")
async def test_output_scorer_async_with_harm_category(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    scorer_id = MagicMock()

    await output_scorer_async(scorer_identifier=scorer_id, harm_category="hate_speech")

    mock_printer.write_async.assert_called_once_with(scorer_identifier=scorer_id, harm_category="hate_speech")


async def test_output_scorer_async_unsupported_format():
    with pytest.raises(ValueError, match="Unsupported format"):
        await output_scorer_async(scorer_identifier=MagicMock(), format="markdown")


# --- output_conversation_async tests ---


@patch("pyrit.output.helpers.PrettyConversationMemoryPrinter")
async def test_output_conversation_async_pretty_default(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    messages = [MagicMock()]

    await output_conversation_async(messages)

    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    assert isinstance(call_kwargs["sink"], StdoutSink)
    mock_printer.write_async.assert_called_once()


@patch("pyrit.output.helpers.PrettyConversationMemoryPrinter")
async def test_output_conversation_async_with_scores(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    messages = [MagicMock()]

    await output_conversation_async(messages, include_scores=True)

    mock_printer.write_async.assert_called_once_with(messages, include_scores=True, include_reasoning_trace=False)


async def test_output_conversation_async_unsupported_format():
    with pytest.raises(ValueError, match="Unsupported format"):
        await output_conversation_async([MagicMock()], format="markdown")


# --- output_score_async tests ---


@patch("pyrit.output.helpers.PrettyScorePrinter")
async def test_output_score_async_pretty_default(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    scores = [MagicMock()]

    await output_score_async(scores)

    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    assert isinstance(call_kwargs["sink"], StdoutSink)
    mock_printer.write_async.assert_called_once_with(scores)


async def test_output_score_async_unsupported_format():
    with pytest.raises(ValueError, match="Unsupported format"):
        await output_score_async([MagicMock()], format="markdown")
