# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re
from unittest.mock import patch

import pytest

from pyrit.converter import AnsiAttackConverter
from pyrit.executor.promptgen.fuzzer import FuzzerResult, FuzzerResultPrinter
from pyrit.memory import CentralMemory, SQLiteMemory
from pyrit.models import MessagePiece, Score, ScoreStatus

# The printer's own colors are SGR sequences, so those are stripped before asserting - but only
# when the printer was asked for them. With colors disabled nothing may reach the terminal.
_OWN_COLORS = re.compile(r"\x1b\[[0-9;]*m")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _assert_no_live_control_characters(printed: str, *, enable_colors: bool) -> None:
    stripped = _OWN_COLORS.sub("", printed) if enable_colors else printed
    assert not _CONTROL_CHARACTERS.search(stripped)
    assert "\\x" in printed


@pytest.mark.parametrize("conversation_ids", [[], ["jailbreak"]])
async def test_result_string_does_not_access_memory_async(conversation_ids: list[str]) -> None:
    result = FuzzerResult(
        successful_templates=["template\x1b[2J"],
        jailbreak_conversation_ids=conversation_ids,
        total_queries=25,
        templates_explored=10,
    )
    with patch.object(
        CentralMemory, "get_memory_instance", side_effect=AssertionError("String conversion read memory")
    ):
        summary = str(result)

    assert "Total Queries: 25" in summary
    assert "Templates Explored: 10" in summary
    assert "Successful Templates: 1" in summary
    assert f"Jailbreak Conversations: {len(conversation_ids)}" in summary
    assert "template\\x1b[2J" in summary
    _assert_no_live_control_characters(summary, enable_colors=False)


@pytest.mark.usefixtures("patch_central_database")
class TestFuzzerResultPrinter:
    @pytest.mark.parametrize("undetermined", [False, True])
    async def test_formatted_result_reads_conversation_and_scores_async(
        self, sqlite_instance: SQLiteMemory, capsys: pytest.CaptureFixture[str], undetermined: bool
    ) -> None:
        request = MessagePiece(role="user", original_value="test question", conversation_id="jailbreak")
        response = MessagePiece(role="assistant", original_value="test answer", conversation_id="jailbreak")
        for piece in (request, response):
            await sqlite_instance.add_message_to_memory_async(request=piece.to_message())
        await sqlite_instance.add_scores_to_memory_async(
            scores=[
                Score(
                    score_type="true_false",
                    score_value=None if undetermined else "true",
                    status=ScoreStatus.UNDETERMINED if undetermined else ScoreStatus.COMPLETE,
                    score_rationale="judge explanation",
                    message_piece_id=response.id,
                )
            ]
        )
        result = FuzzerResult(jailbreak_conversation_ids=["jailbreak"])
        with (
            patch.object(sqlite_instance, "get_message_pieces", side_effect=AssertionError("Sync read")),
            patch.object(sqlite_instance, "get_prompt_scores", side_effect=AssertionError("Sync score read")),
        ):
            await result.print_formatted_async(enable_colors=False, width=80)

        output = capsys.readouterr().out
        assert "Conversation 1 (ID: jailbreak)" in output
        assert "USER:" in output and "test question" in output
        assert "ASSISTANT:" in output and "test answer" in output
        value = "undetermined" if undetermined else "True"
        assert f"Score: {value} | judge explanation" in output
        assert output.count("Score:") == 1
        assert "\x1b" not in output

    @pytest.mark.parametrize("conversation_ids", [[], ["missing"]])
    async def test_formatted_result_handles_missing_conversations(
        self, capsys: pytest.CaptureFixture[str], conversation_ids: list[str]
    ) -> None:
        await FuzzerResult(jailbreak_conversation_ids=conversation_ids).print_formatted_async(enable_colors=False)

        output = capsys.readouterr().out
        expected = "No conversation data found" if conversation_ids else "No jailbreak conversations found"
        assert expected in output
        assert "End of Fuzzer Results" in output

    def test_legacy_formatted_result_warns_and_preserves_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.warns(DeprecationWarning, match="FuzzerResult.print_formatted"):
            FuzzerResult(successful_templates=["example {{ prompt }}"]).print_formatted(enable_colors=False)
        output = capsys.readouterr().out
        assert "example {{ prompt }}" in output
        assert "End of Fuzzer Results" in output

    @pytest.mark.parametrize("enable_colors", [True, False])
    @pytest.mark.parametrize("payload", AnsiAttackConverter.LIVE_PAYLOADS)
    async def test_print_result_escapes_control_characters(self, payload: str, enable_colors: bool, capsys) -> None:
        result = FuzzerResult(successful_templates=[f"template {payload} {{{{ prompt }}}}"])

        (await FuzzerResultPrinter(enable_colors=enable_colors).print_result_async(result))

        _assert_no_live_control_characters(capsys.readouterr().out, enable_colors=enable_colors)

    @pytest.mark.parametrize("payload", AnsiAttackConverter.LIVE_PAYLOADS)
    def test_print_templates_only_escapes_control_characters(self, payload: str, capsys) -> None:
        result = FuzzerResult(successful_templates=[f"template {payload} {{{{ prompt }}}}"])

        FuzzerResultPrinter().print_templates_only(result)

        _assert_no_live_control_characters(capsys.readouterr().out, enable_colors=False)

    async def test_print_result_escapes_carriage_returns(self, capsys) -> None:
        # print_result wraps before it colors, and textwrap replaces a lone carriage return with a
        # space, so the template has to be escaped before it is wrapped.
        result = FuzzerResult(successful_templates=["alpha\rbeta {{ prompt }}"])

        (await FuzzerResultPrinter(enable_colors=False).print_result_async(result))

        assert "alpha\\rbeta" in capsys.readouterr().out
