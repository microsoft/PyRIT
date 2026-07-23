# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest
from colorama import Fore
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


async def test_pretty_reasoning_uses_literal_tags_and_spacing(pretty_printer, reasoning_message):
    rendered = await pretty_printer.render_async([reasoning_message], include_reasoning_trace=True)

    assert "<reasoning-summary>" in rendered
    assert "</reasoning-summary>\n\n  Final answer." in rendered
    assert "Provider-generated reasoning summary (not raw chain-of-thought)" in rendered
    assert "step one" in rendered
    assert "step two" in rendered


async def test_markdown_reasoning_uses_escaped_literal_tags_and_spacing(markdown_printer, reasoning_message):
    rendered = await markdown_printer.render_async([reasoning_message], include_reasoning_trace=True)

    expected = (
        r"\<reasoning-summary\>" + "\n"
        "> **Provider-generated reasoning summary (not raw chain-of-thought)**\n"
        "> step one\n"
        "> step two\n"
        r"\</reasoning-summary\>" + "\n\n"
        "Final answer."
    )
    assert expected in rendered
    assert "<reasoning-summary>" not in rendered


@pytest.mark.parametrize("format_name", ["pretty", "markdown"])
async def test_reasoning_is_hidden_by_default(
    format_name,
    pretty_printer,
    markdown_printer,
    reasoning_message,
):
    printer = pretty_printer if format_name == "pretty" else markdown_printer
    rendered = await printer.render_async([reasoning_message])

    assert "reasoning-summary" not in rendered
    assert "step one" not in rendered
    assert "Final answer." in rendered


@pytest.mark.parametrize("format_name", ["pretty", "markdown"])
async def test_empty_reasoning_summary_omits_tags(
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

    rendered = await printer.render_async([message], include_reasoning_trace=True)

    assert "reasoning-summary" not in rendered
    assert "Final answer." in rendered


@pytest.mark.parametrize(
    "reasoning_value",
    [
        "not-json",
        json.dumps({"type": "message", "summary": []}),
        json.dumps({"type": "reasoning", "summary": []}),
        json.dumps({"id": "r1", "type": "reasoning"}),
        json.dumps({"id": "r1", "type": "reasoning", "summary": [{"type": "text", "text": "step"}]}),
        json.dumps({"id": "r1", "type": "reasoning", "summary": [{"type": "summary_text", "text": 1}]}),
        json.dumps({"id": "r1", "type": "reasoning", "summary": [], "status": "invalid"}),
        json.dumps({"id": "r1", "type": "reasoning", "summary": [], "content": [{"type": "text"}]}),
    ],
)
async def test_reasoning_payload_must_match_openai_responses_shape(markdown_printer, reasoning_value):
    message = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=reasoning_value,
                converted_value=reasoning_value,
                original_value_data_type="reasoning",
                converted_value_data_type="reasoning",
            )
        ]
    )

    with pytest.raises(ValueError, match="Reasoning piece|reasoning summary item|reasoning content item"):
        await markdown_printer.render_async([message], include_reasoning_trace=True)


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

    rendered = await markdown_printer.render_async([message], include_reasoning_trace=True)

    assert "converted reasoning leak" not in rendered
    assert "step one" in rendered


async def test_pretty_reasoning_is_gray_and_answer_keeps_assistant_color(
    patch_central_database,
    reasoning_message,
):
    printer = PrettyConversationMemoryPrinter(enable_colors=True)

    rendered = await printer.render_async([reasoning_message], include_reasoning_trace=True)

    assert f"{Fore.LIGHTBLACK_EX}  <reasoning-summary>" in rendered
    assert f"{Fore.LIGHTBLACK_EX}  step one" in rendered
    assert f"{Fore.YELLOW}  Final answer." in rendered
