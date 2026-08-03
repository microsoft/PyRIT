# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest
from colorama import Fore, Style
from pydantic import ValidationError

from pyrit.models import Message, MessagePiece
from pyrit.output.conversation.markdown import MarkdownConversationMemoryPrinter
from pyrit.output.conversation.pretty import PrettyConversationMemoryPrinter


@pytest.fixture
def pretty_printer(patch_central_database) -> PrettyConversationMemoryPrinter:
    return PrettyConversationMemoryPrinter(enable_colors=False)


@pytest.fixture
def markdown_printer(patch_central_database) -> MarkdownConversationMemoryPrinter:
    return MarkdownConversationMemoryPrinter()


async def test_pretty_reasoning_uses_headings_and_spacing(pretty_printer, reasoning_message):
    rendered = await pretty_printer.render_async([reasoning_message], include_reasoning_summaries=True)

    assert "💭 Reasoning" in rendered
    assert "step two\n\n  💬 Response\n  Final answer." in rendered
    assert "Provider-generated reasoning summary (not raw chain-of-thought)" in rendered
    assert "step one" in rendered
    assert "step two" in rendered


async def test_markdown_reasoning_uses_headings_and_spacing(markdown_printer, reasoning_message):
    rendered = await markdown_printer.render_async([reasoning_message], include_reasoning_summaries=True)

    expected = (
        "> **💭 Reasoning**\n"
        "> *Provider-generated summary (not raw chain-of-thought)*\n"
        "> step one\n"
        "> step two\n"
        "\n"
        "**💬 Response**\n\n"
        "Final answer."
    )
    assert expected in rendered


@pytest.mark.parametrize("format_name", ["pretty", "markdown"])
async def test_reasoning_only_message_omits_response_heading(
    format_name,
    pretty_printer,
    markdown_printer,
    reasoning_value,
):
    message = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=reasoning_value,
                original_value_data_type="reasoning",
            )
        ]
    )
    printer = pretty_printer if format_name == "pretty" else markdown_printer

    rendered = await printer.render_async([message], include_reasoning_summaries=True)

    assert "💭 Reasoning" in rendered
    assert "💬 Response" not in rendered


@pytest.mark.parametrize("format_name", ["pretty", "markdown"])
async def test_reasoning_is_hidden_by_default(
    format_name,
    pretty_printer,
    markdown_printer,
    reasoning_message,
):
    printer = pretty_printer if format_name == "pretty" else markdown_printer
    rendered = await printer.render_async([reasoning_message])

    assert "💭 Reasoning" not in rendered
    assert "💬 Response" not in rendered
    assert "step one" not in rendered
    assert "Final answer." in rendered


@pytest.mark.parametrize("format_name", ["pretty", "markdown"])
async def test_absent_reasoning_piece_renders_no_reasoning_state(
    format_name,
    pretty_printer,
    markdown_printer,
):
    message = Message(message_pieces=[MessagePiece(role="assistant", original_value="Final answer.")])
    printer = pretty_printer if format_name == "pretty" else markdown_printer

    rendered = await printer.render_async([message], include_reasoning_summaries=True)

    assert "💭 Reasoning" not in rendered
    assert "No reasoning summary was returned by the provider." not in rendered
    assert "💬 Response" not in rendered
    assert "Final answer." in rendered


@pytest.mark.parametrize("format_name", ["pretty", "markdown"])
async def test_empty_reasoning_summary_renders_explicit_state(
    format_name,
    pretty_printer,
    markdown_printer,
    empty_reasoning_value,
):
    message = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=empty_reasoning_value,
                converted_value=empty_reasoning_value,
                original_value_data_type="reasoning",
                converted_value_data_type="reasoning",
            ),
            MessagePiece(role="assistant", original_value="Final answer."),
        ]
    )
    printer = pretty_printer if format_name == "pretty" else markdown_printer

    rendered = await printer.render_async([message], include_reasoning_summaries=True)

    assert "💭 Reasoning" in rendered
    assert "[No reasoning summary was returned by the provider.]" in rendered
    assert "💬 Response" in rendered
    assert "Final answer." in rendered


@pytest.mark.parametrize(
    "reasoning_value",
    [
        "not-json",
        json.dumps([]),
        json.dumps({}),
        json.dumps({"summary": None}),
        json.dumps({"summary": "step"}),
        json.dumps({"summary": [None]}),
        json.dumps({"summary": [{}]}),
        json.dumps({"summary": [{"text": 1}]}),
    ],
)
def test_reasoning_payload_requires_renderable_summary_fields(reasoning_value):
    with pytest.raises(ValueError, match="Reasoning piece|reasoning summary item"):
        MarkdownConversationMemoryPrinter._extract_reasoning_summary(reasoning_value)


@pytest.mark.parametrize("format_name", ["pretty", "markdown"])
async def test_malformed_reasoning_warns_and_preserves_response(
    format_name,
    pretty_printer,
    markdown_printer,
):
    message = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value="not-json",
                original_value_data_type="reasoning",
            ),
            MessagePiece(role="assistant", original_value="Final answer."),
        ]
    )
    printer = pretty_printer if format_name == "pretty" else markdown_printer

    rendered = await printer.render_async([message], include_reasoning_summaries=True)

    assert "⚠ WARNING: Reasoning summary failed to render; conversation is intact." in rendered
    assert "💬 Response" in rendered
    assert "Final answer." in rendered


async def test_pretty_malformed_reasoning_warning_is_red(patch_central_database):
    printer = PrettyConversationMemoryPrinter(enable_colors=True)
    message = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value="not-json",
                original_value_data_type="reasoning",
            )
        ]
    )

    rendered = await printer.render_async([message], include_reasoning_summaries=True)

    expected = f"{Style.BRIGHT}{Fore.RED}  ⚠ WARNING: Reasoning summary failed to render; conversation is intact."
    assert expected in rendered


async def test_reasoning_payload_ignores_unrendered_fields(markdown_printer):
    reasoning_value = json.dumps(
        {
            "type": "unexpected",
            "id": 42,
            "summary": [{"type": "unexpected", "text": "rendered summary"}],
            "status": "unexpected",
            "encrypted_content": 42,
            "content": "unexpected",
        }
    )
    message = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=reasoning_value,
                original_value_data_type="reasoning",
            )
        ]
    )

    rendered = await markdown_printer.render_async([message], include_reasoning_summaries=True)

    assert "rendered summary" in rendered


def test_extract_reasoning_summary_preserves_extremely_long_text():
    long_summary = f"start {'reasoning ' * 10_000}end"
    reasoning_value = json.dumps({"summary": [{"text": long_summary}]})

    extracted = MarkdownConversationMemoryPrinter._extract_reasoning_summary(reasoning_value)

    assert extracted == long_summary


def test_reasoning_prompt_data_type_requires_exact_literal():
    with pytest.raises(ValidationError):
        MessagePiece(
            role="assistant",
            original_value="{}",
            converted_value_data_type="thinking",  # type: ignore[arg-type]
        )


async def test_original_reasoning_converted_to_text_remains_hidden_by_default(
    markdown_printer,
    reasoning_value,
):
    message = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=reasoning_value,
                converted_value="converted reasoning leak",
                original_value_data_type="reasoning",
                converted_value_data_type="text",
            ),
            MessagePiece(role="assistant", original_value="Final answer."),
        ]
    )

    rendered = await markdown_printer.render_async([message])

    assert "converted reasoning leak" not in rendered
    assert "step one" not in rendered
    assert "Final answer." in rendered


async def test_original_reasoning_converted_to_text_uses_original_reasoning_payload(
    markdown_printer,
    reasoning_value,
):
    message = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=reasoning_value,
                converted_value="converted reasoning leak",
                original_value_data_type="reasoning",
                converted_value_data_type="text",
            ),
        ]
    )

    rendered = await markdown_printer.render_async([message], include_reasoning_summaries=True)

    assert "converted reasoning leak" not in rendered
    assert "step one" in rendered


async def test_pretty_reasoning_is_gray_and_answer_keeps_assistant_color(
    patch_central_database,
    reasoning_message,
):
    printer = PrettyConversationMemoryPrinter(enable_colors=True)

    rendered = await printer.render_async([reasoning_message], include_reasoning_summaries=True)

    assert f"{Fore.LIGHTBLACK_EX}  💭 Reasoning" in rendered
    assert f"{Fore.LIGHTBLACK_EX}  step one" in rendered
    assert f"{Fore.YELLOW}  💬 Response" in rendered
    assert f"{Fore.YELLOW}  Final answer." in rendered
