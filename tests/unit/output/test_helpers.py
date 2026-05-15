# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.output.helpers import (
    print_attack_result_async,
    print_conversation_async,
    print_scenario_result_async,
    print_score_async,
    print_scorer_async,
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


# --- print_attack_result_async tests ---


@patch("pyrit.output.attack_result.pretty.PrettyAttackResultMemoryPrinter")
async def test_print_attack_result_async_pretty_default(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()

    await print_attack_result_async(result)

    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    assert isinstance(call_kwargs["sink"], StdoutSink)
    mock_printer.write_async.assert_called_once()


@patch("pyrit.output.attack_result.markdown.MarkdownAttackResultMemoryPrinter")
async def test_print_attack_result_async_markdown_auto_detects_sink(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()

    await print_attack_result_async(result, format="markdown")

    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    # Outside a notebook, auto-detect falls back to StdoutSink
    assert isinstance(call_kwargs["sink"], StdoutSink)
    mock_printer.write_async.assert_called_once()


@patch("pyrit.output.attack_result.pretty.PrettyAttackResultMemoryPrinter")
async def test_print_attack_result_async_explicit_sink(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    result = MagicMock()
    custom_sink = StdoutSink()

    await print_attack_result_async(result, sink=custom_sink)

    call_kwargs = mock_cls.call_args[1]
    assert call_kwargs["sink"] is custom_sink


# --- print_scenario_result_async tests ---


@patch("pyrit.output.scenario_result.pretty.PrettyScenarioResultMemoryPrinter")
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


@patch("pyrit.output.scorer.pretty.PrettyScorerMemoryPrinter")
async def test_print_scorer_async_pretty(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    scorer_id = MagicMock()

    await print_scorer_async(scorer_identifier=scorer_id)

    mock_cls.assert_called_once()
    mock_printer.write_async.assert_called_once_with(scorer_identifier=scorer_id, harm_category=None)


@patch("pyrit.output.scorer.pretty.PrettyScorerMemoryPrinter")
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


# --- print_conversation_async tests ---


@patch("pyrit.output.conversation.pretty.PrettyConversationMemoryPrinter")
async def test_print_conversation_async_pretty_default(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    messages = [MagicMock()]

    await print_conversation_async(messages)

    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    assert isinstance(call_kwargs["sink"], StdoutSink)
    mock_printer.write_async.assert_called_once()


@patch("pyrit.output.conversation.pretty.PrettyConversationMemoryPrinter")
async def test_print_conversation_async_with_scores(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    messages = [MagicMock()]

    await print_conversation_async(messages, include_scores=True)

    mock_printer.write_async.assert_called_once_with(messages, include_scores=True, include_reasoning_trace=False)


async def test_print_conversation_async_unsupported_format():
    with pytest.raises(ValueError, match="Unsupported format"):
        await print_conversation_async([MagicMock()], format="markdown")


# --- print_score_async tests ---


@patch("pyrit.output.score.pretty.PrettyScorePrinter")
async def test_print_score_async_pretty_default(mock_cls):
    mock_printer = MagicMock()
    mock_printer.write_async = AsyncMock()
    mock_cls.return_value = mock_printer
    scores = [MagicMock()]

    await print_score_async(scores)

    mock_cls.assert_called_once()
    call_kwargs = mock_cls.call_args[1]
    assert isinstance(call_kwargs["sink"], StdoutSink)
    mock_printer.write_async.assert_called_once_with(scores)


async def test_print_score_async_unsupported_format():
    with pytest.raises(ValueError, match="Unsupported format"):
        await print_score_async([MagicMock()], format="markdown")
