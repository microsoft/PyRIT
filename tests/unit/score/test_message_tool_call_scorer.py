# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai.types.responses import ResponseOutputText

from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    AttackOutcome,
    ContentScorable,
    Message,
    MessagePiece,
    MessageScorable,
    ScoreStatus,
    ScoringExpectation,
    ToolCallRequirement,
    ToolsCalled,
)
from pyrit.prompt_target import OpenAIResponseTarget
from pyrit.score import (
    InMemoryTraceClient,
    MessageToolCallScorer,
    OtelToolCallScorer,
    OtelTraceSource,
    TrueFalseCompositeScorer,
    TrueFalseScoreAggregator,
)

pytestmark = pytest.mark.usefixtures("patch_central_database")


def _expectation(*names: str) -> ScoringExpectation:
    return ScoringExpectation(conditions=(ToolsCalled(tools=tuple(ToolCallRequirement(name=name) for name in names)),))


def _responses_call(call_id: str, name: str) -> dict:
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": "{}"}


def _chat_completions_call(call_id: str, name: str) -> dict:
    return {"type": "function", "id": call_id, "function": {"name": name, "arguments": "{}"}}


def _output(call_id: str, output: object) -> dict:
    text = output if isinstance(output, str) else json.dumps(output, separators=(",", ":"))
    return {"type": "function_call_output", "call_id": call_id, "output": text}


class _Conversation:
    """Store pieces in one conversation, one message per piece, in call order."""

    def __init__(self, memory: SQLiteMemory) -> None:
        self._memory = memory
        self.conversation_id = str(uuid.uuid4())
        self.add("user", "text", "use the tools")

    def add(self, role: str, data_type: str, value: object) -> Message:
        text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
        piece = MessagePiece(
            role=role,
            original_value=text,
            original_value_data_type=data_type,
            conversation_id=self.conversation_id,
        )
        message = piece.to_message()
        self._memory.add_message_to_memory(request=message)
        return message

    def call(self, call: dict, *, role: str = "assistant") -> Message:
        return self.add(role, "function_call", call)

    def output(self, call_id: str, output: object = "ok") -> Message:
        return self.add("tool", "function_call_output", _output(call_id, output))

    def reply(self, text: str = "done") -> Message:
        return self.add("assistant", "text", text)


async def _score(message: Message, *names: str):
    scorer = MessageToolCallScorer()
    scores = await scorer.score_async(scorable=MessageScorable.from_message(message), expectation=_expectation(*names))
    assert len(scores) == 1
    return scores[0]


@pytest.mark.parametrize("call_format", [_responses_call, _chat_completions_call])
async def test_paired_output_is_true_async(sqlite_instance: SQLiteMemory, call_format) -> None:
    conversation = _Conversation(sqlite_instance)
    conversation.call(call_format("call_1", "lookup"))
    conversation.output("call_1")
    reply = conversation.reply()

    score = await _score(reply, "lookup")

    assert score.status == ScoreStatus.COMPLETE
    assert score.get_value() is True
    assert score.message_piece_id == reply.get_piece().id
    assert score.scorable == MessageScorable.from_message(reply)
    assert "lookup" in score.score_rationale


@pytest.mark.parametrize("call_format", [_responses_call, _chat_completions_call])
async def test_requested_call_without_output_is_undetermined_async(sqlite_instance: SQLiteMemory, call_format) -> None:
    conversation = _Conversation(sqlite_instance)
    request = conversation.call(call_format("call_1", "lookup"))

    score = await _score(request, "lookup")

    assert score.status == ScoreStatus.UNDETERMINED
    assert score.score_value is None
    assert "lookup" in score.score_rationale


async def test_no_tool_evidence_is_undetermined_not_false_async(sqlite_instance: SQLiteMemory) -> None:
    reply = _Conversation(sqlite_instance).reply()

    score = await _score(reply, "lookup")

    assert score.status == ScoreStatus.UNDETERMINED
    assert score.score_value is None


@pytest.mark.parametrize(
    "dispatch_error",
    [
        {"error": "function_not_found", "missing_function": "lookup", "available_functions": []},
        {"error": "missing_function_name", "tool_call_section": {"type": "function_call"}},
        {"error": "malformed_arguments", "function": "lookup", "raw_arguments": "{"},
    ],
)
async def test_tolerant_dispatch_error_is_not_execution_async(sqlite_instance: SQLiteMemory, dispatch_error) -> None:
    conversation = _Conversation(sqlite_instance)
    conversation.call(_responses_call("call_1", "lookup"))
    conversation.output("call_1", dispatch_error)
    reply = conversation.reply()

    score = await _score(reply, "lookup")

    assert score.status == ScoreStatus.UNDETERMINED


@pytest.mark.parametrize("output", [{"error": "function_not_found"}, {"error": "rate limited"}, "not json", ""])
async def test_tool_reported_error_still_counts_as_execution_async(sqlite_instance: SQLiteMemory, output) -> None:
    conversation = _Conversation(sqlite_instance)
    conversation.call(_responses_call("call_1", "lookup"))
    conversation.output("call_1", output)
    reply = conversation.reply()

    assert (await _score(reply, "lookup")).get_value() is True


async def test_output_must_pair_by_call_id_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = _Conversation(sqlite_instance)
    conversation.call(_responses_call("call_1", "lookup"))
    conversation.call(_responses_call("call_2", "delete"))
    conversation.output("call_2")
    reply = conversation.reply()

    assert (await _score(reply, "delete")).get_value() is True
    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


async def test_output_for_an_unknown_call_is_ignored_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = _Conversation(sqlite_instance)
    conversation.call(_responses_call("call_1", "lookup"))
    conversation.output("call_9")
    reply = conversation.reply()

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


@pytest.mark.parametrize(("role", "data_type"), [("assistant", "function_call_output"), ("tool", "text")])
async def test_only_tool_role_function_call_outputs_count_async(
    sqlite_instance: SQLiteMemory, role: str, data_type: str
) -> None:
    conversation = _Conversation(sqlite_instance)
    conversation.call(_responses_call("call_1", "lookup"))
    conversation.add(role, data_type, _output("call_1", "ok"))
    reply = conversation.reply()

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


async def test_output_before_its_request_is_ignored_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = _Conversation(sqlite_instance)
    conversation.output("call_1")
    conversation.call(_responses_call("call_1", "lookup"))
    reply = conversation.reply()

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


async def test_every_required_tool_must_run_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = _Conversation(sqlite_instance)
    conversation.call(_responses_call("call_1", "lookup"))
    conversation.output("call_1")
    partial = conversation.reply()
    conversation.call(_responses_call("call_2", "summarize"))
    conversation.output("call_2")
    full = conversation.reply()

    missing = await _score(partial, "lookup", "summarize")
    assert missing.status == ScoreStatus.UNDETERMINED
    assert "summarize" in missing.score_rationale
    assert "lookup" not in missing.score_rationale.split(".")[0]
    assert (await _score(full, "lookup", "summarize")).get_value() is True


async def test_tool_names_match_exactly_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = _Conversation(sqlite_instance)
    conversation.call(_responses_call("call_1", "Lookup"))
    conversation.output("call_1")
    reply = conversation.reply()

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


async def test_later_turns_are_not_evidence_for_an_earlier_message_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = _Conversation(sqlite_instance)
    earlier = conversation.reply()
    conversation.call(_responses_call("call_1", "lookup"))
    conversation.output("call_1")

    assert (await _score(earlier, "lookup")).status == ScoreStatus.UNDETERMINED


async def test_other_conversations_are_not_evidence_async(sqlite_instance: SQLiteMemory) -> None:
    other = _Conversation(sqlite_instance)
    other.call(_responses_call("call_1", "lookup"))
    other.output("call_1")
    conversation = _Conversation(sqlite_instance)
    for _ in range(3):
        reply = conversation.reply()

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


@pytest.mark.parametrize("role", ["simulated_assistant", "user"])
async def test_calls_the_model_did_not_make_are_ignored_async(sqlite_instance: SQLiteMemory, role: str) -> None:
    conversation = _Conversation(sqlite_instance)
    conversation.call(_responses_call("call_1", "lookup"), role=role)
    conversation.output("call_1")
    reply = conversation.reply()

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


async def test_function_call_text_in_a_reply_is_not_a_call_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = _Conversation(sqlite_instance)
    conversation.add("assistant", "text", _responses_call("call_1", "lookup"))
    conversation.output("call_1")
    reply = conversation.reply()

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


async def test_requires_a_tools_called_condition_async(sqlite_instance: SQLiteMemory) -> None:
    reply = _Conversation(sqlite_instance).reply()

    with pytest.raises(ValueError, match="ToolsCalled"):
        await MessageToolCallScorer().score_async(
            scorable=MessageScorable.from_message(reply), expectation=ScoringExpectation()
        )


async def test_rejects_loose_content_async() -> None:
    with pytest.raises(RuntimeError, match="requires a MessageScorable") as error:
        await MessageToolCallScorer().score_async(
            scorable=ContentScorable(value="I called lookup"), expectation=_expectation("lookup")
        )
    assert isinstance(error.value.__cause__, TypeError)


@pytest.mark.parametrize("executed", [True, False])
async def test_composes_under_or_with_trace_scoring_async(sqlite_instance: SQLiteMemory, executed: bool) -> None:
    conversation = _Conversation(sqlite_instance)
    conversation.call(_responses_call("call_1", "lookup"))
    if executed:
        conversation.output("call_1")
    reply = conversation.reply()
    client = InMemoryTraceClient()
    composite = TrueFalseCompositeScorer(
        scorers=[OtelToolCallScorer(source=OtelTraceSource(trace_client=client)), MessageToolCallScorer()],
        aggregator=TrueFalseScoreAggregator.OR,
    )

    scores = await composite.score_async(
        scorable=MessageScorable.from_message(reply), expectation=_expectation("lookup")
    )

    assert len(scores) == 1
    if executed:
        assert scores[0].get_value() is True
    else:
        assert scores[0].status == ScoreStatus.UNDETERMINED
    client.close()


def _sdk_function_call(call_id: str, name: str) -> MagicMock:
    section = MagicMock()
    section.type = "function_call"
    section.call_id = call_id
    section.name = name
    section.arguments = "{}"
    return MagicMock(status="completed", error=None, output=[section])


def _sdk_text(text: str) -> MagicMock:
    section = MagicMock()
    section.type = "message"
    section.content = [ResponseOutputText(annotations=[], text=text, type="output_text")]
    return MagicMock(status="completed", error=None, output=[section])


@pytest.mark.parametrize(
    ("registered", "outcome"),
    [(True, AttackOutcome.SUCCESS), (False, AttackOutcome.UNDETERMINED)],
)
async def test_scores_the_response_target_tool_loop_async(registered: bool, outcome: AttackOutcome) -> None:
    target = OpenAIResponseTarget(
        model_name="gpt-4", endpoint="https://mock.azure.com", api_key="mock-key", fail_on_missing_function=False
    )
    if registered:

        async def lookup(args: dict) -> dict:
            return {"found": True}

        target._custom_functions["lookup"] = lookup
    attack = PromptSendingAttack(
        objective_target=target,
        attack_scoring_config=AttackScoringConfig(objective_scorer=MessageToolCallScorer()),
        max_attempts_on_failure=0,
    )
    responses = [_sdk_function_call("call_1", "lookup"), _sdk_text("found it")]
    with patch.object(target._async_client.responses, "create", new_callable=AsyncMock, side_effect=responses):
        result = await attack.execute_async(objective="look it up", expectation=_expectation("lookup"))

    assert result.outcome is outcome
    assert result.automated_score is not None
    assert result.automated_score.message_piece_id == result.last_response.id
