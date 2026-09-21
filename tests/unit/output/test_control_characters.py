# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests that target-controlled text reaches the console without live control characters."""

import re

import pytest

from pyrit.converter import AnsiAttackConverter
from pyrit.models import AttackOutcome, AttackResult, Message, MessagePiece, Score
from pyrit.output.attack_result.pretty import PrettyAttackResultMemoryPrinter
from pyrit.output.conversation.pretty import PrettyConversationPrinter
from pyrit.output.score.pretty import PrettyScorePrinter

pytestmark = pytest.mark.usefixtures("patch_central_database")

# The printers' own colors are SGR sequences, so those are stripped before asserting - but only
# when the printer was asked for them. With colors disabled nothing may reach the terminal.
_OWN_COLORS = re.compile(r"\x1b\[[0-9;]*m")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

# An SGR sequence shaped exactly like the printers' own colors, so it pins that a target's colors
# are escaped rather than passed through.
_TARGET_COLOR_PAYLOAD = "Hello \x1b[32mTHIS IS GREEN\x1b[0m\x07"


def _assert_no_live_control_characters(rendered: str, *, enable_colors: bool) -> None:
    stripped = _OWN_COLORS.sub("", rendered) if enable_colors else rendered
    assert not _CONTROL_CHARACTERS.search(stripped)
    assert "\\x" in rendered


class _ScoreSource:
    def __init__(self, scores: list[Score]) -> None:
        self._scores = scores

    async def get_messages_async(self, *, conversation_id: str) -> list[Message]:
        return []

    async def get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        return self._scores


@pytest.mark.parametrize("enable_colors", [True, False])
@pytest.mark.parametrize("payload", AnsiAttackConverter.LIVE_PAYLOADS)
async def test_conversation_printer_escapes_target_controlled_text(payload: str, enable_colors: bool) -> None:
    prompt = MessagePiece(role="user", original_value=f"original {payload}", converted_value=f"converted {payload}")
    response = MessagePiece(role="assistant", original_value=f"response {payload}")
    score = Score(score_value="true", score_type="true_false", score_rationale=f"quoted {payload}")
    printer = PrettyConversationPrinter(source=_ScoreSource([score]), enable_colors=enable_colors)

    rendered = await printer.render_async(
        [Message(message_pieces=[prompt]), Message(message_pieces=[response])], include_scores=True
    )

    _assert_no_live_control_characters(rendered, enable_colors=enable_colors)


@pytest.mark.parametrize("enable_colors", [True, False])
@pytest.mark.parametrize("payload", AnsiAttackConverter.LIVE_PAYLOADS)
async def test_score_printer_escapes_rationale(payload: str, enable_colors: bool) -> None:
    score = Score(score_value="true", score_type="true_false", score_rationale=f"quoted {payload}")

    rendered = await PrettyScorePrinter(enable_colors=enable_colors).render_async([score])

    _assert_no_live_control_characters(rendered, enable_colors=enable_colors)


@pytest.mark.parametrize("enable_colors", [True, False])
@pytest.mark.parametrize("payload", AnsiAttackConverter.LIVE_PAYLOADS)
async def test_attack_result_printer_escapes_summary(payload: str, enable_colors: bool) -> None:
    printer = PrettyAttackResultMemoryPrinter(enable_colors=enable_colors)
    result = AttackResult(
        conversation_id="conversation-id",
        objective=f"objective {payload}",
        outcome=AttackOutcome.FAILURE,
        outcome_reason=f"reason {payload}",
        metadata={"note": f"metadata {payload}"},
    )

    rendered = await printer.render_async(result)

    _assert_no_live_control_characters(rendered, enable_colors=enable_colors)


async def test_conversation_printer_escapes_target_color_codes() -> None:
    response = MessagePiece(role="assistant", original_value=_TARGET_COLOR_PAYLOAD)
    printer = PrettyConversationPrinter(source=_ScoreSource([]), enable_colors=False)

    rendered = await printer.render_async([Message(message_pieces=[response])])

    assert "\\x1b[32m" in rendered
    assert "\x1b[32m" not in rendered


async def test_conversation_printer_handles_carriage_returns() -> None:
    # The lone carriage return sits past the wrap width, where TextWrapper drops it, and the CRLF
    # must not leave a literal escape at the end of a line.
    value = "A" * 90 + "\r" + "B" * 20 + "\r\nsecond line"
    piece = MessagePiece(role="assistant", original_value=value)
    printer = PrettyConversationPrinter(source=_ScoreSource([]), enable_colors=False)

    rendered = await printer.render_async([Message(message_pieces=[piece])])

    assert "A\\rB" in rendered
    assert "\\r\n" not in rendered
    assert "second line\n" in rendered


async def test_score_printer_handles_carriage_returns() -> None:
    score = Score(score_value="true", score_type="true_false", score_rationale="first line\r\nsecond line\rrewritten")

    rendered = await PrettyScorePrinter(enable_colors=False).render_async([score])

    assert "second line\\rrewritten\n" in rendered
    assert "first line\n" in rendered
