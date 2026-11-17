# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai.types.responses import ResponseOutputText

from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    AttackOutcome,
    ContentScorable,
    Equals,
    Message,
    MessagePiece,
    MessageScorable,
    RequestTraceContext,
    Score,
    ScoreStatus,
    ScoringExpectation,
    ToolArgumentRequirement,
    ToolCallRequirement,
    ToolCaptureCoverage,
    ToolCaptureGap,
    ToolExecutionMetadata,
    ToolsCalled,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import HTTPTarget, OpenAIResponseTarget
from pyrit.score import (
    InMemoryTraceClient,
    MessageToolCallScorer,
    OtelToolCallScorer,
    OtelTraceSource,
    TrueFalseCompositeScorer,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.message_tool_call_scorer import message_tool_capture_gaps

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

    @classmethod
    async def create_async(cls, *, memory: SQLiteMemory) -> "_Conversation":
        conversation = cls(memory)
        await conversation.add_async("user", "text", "use the tools")
        return conversation

    async def add_async(
        self, role: str, data_type: str, value: object, *, metadata: dict[str, Any] | None = None
    ) -> Message:
        text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
        piece = MessagePiece(
            role=role,
            original_value=text,
            original_value_data_type=data_type,
            conversation_id=self.conversation_id,
            prompt_metadata=metadata or {},
        )
        message = piece.to_message()
        await self._memory.add_message_to_memory_async(request=message)
        return message

    async def call_async(self, call: dict, *, role: str = "assistant") -> Message:
        return await self.add_async(role, "function_call", call)

    async def output_async(self, call_id: str, output: object = "ok", *, role: str = "tool") -> Message:
        return await self.add_async(role, "function_call_output", _output(call_id, output))

    async def reply_async(self, text: str = "done") -> Message:
        return await self.add_async("assistant", "text", text)


async def _score(message: Message, *names: str):
    scorer = MessageToolCallScorer()
    scores = await scorer.score_async(scorable=MessageScorable.from_message(message), expectation=_expectation(*names))
    assert len(scores) == 1
    return scores[0]


@pytest.mark.parametrize("call_format", [_responses_call, _chat_completions_call])
async def test_paired_output_is_true_async(sqlite_instance: SQLiteMemory, call_format) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(call_format("call_1", "lookup"))
    await conversation.output_async("call_1")
    reply = await conversation.reply_async()

    score = await _score(reply, "lookup")

    assert score.status == ScoreStatus.COMPLETE
    assert score.get_value() is True
    assert score.message_piece_id == reply.get_piece().id
    assert score.scorable == MessageScorable.from_message(reply)
    assert "lookup" in score.score_rationale


@pytest.mark.parametrize("call_format", [_responses_call, _chat_completions_call])
async def test_requested_call_without_output_is_undetermined_async(sqlite_instance: SQLiteMemory, call_format) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    request = await conversation.call_async(call_format("call_1", "lookup"))

    score = await _score(request, "lookup")

    assert score.status == ScoreStatus.UNDETERMINED
    assert score.score_value is None
    assert "lookup" in score.score_rationale


async def test_no_tool_evidence_is_undetermined_not_false_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    reply = await conversation.reply_async()

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
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("call_1", "lookup"))
    await conversation.output_async("call_1", dispatch_error)
    reply = await conversation.reply_async()

    score = await _score(reply, "lookup")

    assert score.status == ScoreStatus.UNDETERMINED


@pytest.mark.parametrize(
    "output",
    [
        {"error": "function_not_found"},
        {"error": "rate limited"},
        {"error": {"code": "timeout"}},
        {"error": ["timeout"]},
        "not json",
        "",
    ],
)
async def test_tool_reported_error_still_counts_as_execution_async(sqlite_instance: SQLiteMemory, output) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("call_1", "lookup"))
    await conversation.output_async("call_1", output)
    reply = await conversation.reply_async()

    assert (await _score(reply, "lookup")).get_value() is True


async def test_unrelated_structured_error_does_not_abort_scoring_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("other", "other_tool"))
    await conversation.output_async("other", {"error": {"code": "timeout"}})
    await conversation.call_async(_responses_call("call_1", "lookup"))
    await conversation.output_async("call_1")

    assert (await _score(await conversation.reply_async(), "lookup")).get_value() is True


@pytest.mark.parametrize("invoked", [True, False])
@pytest.mark.parametrize("output", ["ok", {"error": "malformed_arguments", "raw_arguments": "{"}])
async def test_stored_execution_metadata_controls_verdict_async(
    sqlite_instance: SQLiteMemory, invoked: bool, output: object
) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("call_1", "lookup"))
    result = await conversation.add_async(
        "tool",
        "function_call_output",
        _output("call_1", output),
        metadata=ToolExecutionMetadata(invoked=invoked).to_metadata(),
    )
    stored = await sqlite_instance.get_message_pieces_async(prompt_ids=[str(result.get_piece().id)])
    assert ToolExecutionMetadata.from_metadata(metadata=stored[0].prompt_metadata) == ToolExecutionMetadata(
        invoked=invoked
    )

    score = await _score(await conversation.reply_async(), "lookup")
    if invoked:
        assert score.get_value() is True
    else:
        assert score.status == ScoreStatus.UNDETERMINED


async def test_invalid_execution_metadata_is_not_a_legacy_fallback_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("call_1", "lookup"))
    await conversation.add_async(
        "tool",
        "function_call_output",
        _output("call_1", "ok"),
        metadata={ToolExecutionMetadata.METADATA_KEY: {"invoked": "false"}},
    )

    with pytest.raises(RuntimeError, match="invoked"):
        await _score(await conversation.reply_async(), "lookup")


async def test_output_must_pair_by_call_id_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("call_1", "lookup"))
    await conversation.call_async(_responses_call("call_2", "delete"))
    await conversation.output_async("call_2")
    reply = await conversation.reply_async()

    assert (await _score(reply, "delete")).get_value() is True
    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


async def test_output_for_an_unknown_call_is_ignored_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("call_1", "lookup"))
    await conversation.output_async("call_9")
    reply = await conversation.reply_async()

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


@pytest.mark.parametrize(("role", "data_type"), [("assistant", "function_call_output"), ("tool", "text")])
async def test_only_tool_role_function_call_outputs_count_async(
    sqlite_instance: SQLiteMemory, role: str, data_type: str
) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("call_1", "lookup"))
    await conversation.add_async(role, data_type, _output("call_1", "ok"))
    reply = await conversation.reply_async()

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


async def test_output_before_its_request_is_ignored_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.output_async("call_1")
    await conversation.call_async(_responses_call("call_1", "lookup"))
    reply = await conversation.reply_async()

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


async def test_every_required_tool_must_run_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("call_1", "lookup"))
    await conversation.output_async("call_1")
    partial = await conversation.reply_async()
    await conversation.call_async(_responses_call("call_2", "summarize"))
    await conversation.output_async("call_2")
    full = await conversation.reply_async()

    missing = await _score(partial, "lookup", "summarize")
    assert missing.status == ScoreStatus.UNDETERMINED
    assert "summarize" in missing.score_rationale
    assert "lookup" not in missing.score_rationale.split(".")[0]
    assert (await _score(full, "lookup", "summarize")).get_value() is True


def _send_email(call_format, call_id: str, arguments: object) -> dict:
    call = call_format(call_id, "send_email")
    text = arguments if isinstance(arguments, str) else json.dumps(arguments)
    if "function" in call:
        call["function"]["arguments"] = text
    else:
        call["arguments"] = text
    return call


def _argument_expectation(**matchers: str) -> ScoringExpectation:
    arguments = tuple(
        ToolArgumentRequirement(name=name, matcher=Equals(value=value)) for name, value in matchers.items()
    )
    return ScoringExpectation(
        conditions=(ToolsCalled(tools=(ToolCallRequirement(name="send_email", arguments=arguments),)),)
    )


async def _score_arguments(message: Message, **matchers: str):
    scorer = MessageToolCallScorer()
    scores = await scorer.score_async(
        scorable=MessageScorable.from_message(message), expectation=_argument_expectation(**matchers)
    )
    assert len(scores) == 1
    return scores[0]


@pytest.mark.parametrize("call_format", [_responses_call, _chat_completions_call])
async def test_executed_call_with_matching_arguments_is_true_async(sqlite_instance: SQLiteMemory, call_format) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_send_email(call_format, "call_1", {"to": "a@example.com", "subject": "hi"}))
    await conversation.output_async("call_1")
    reply = await conversation.reply_async()

    score = await _score_arguments(reply, to="a@example.com")

    assert score.get_value() is True
    assert "send_email(to)" in score.score_rationale


@pytest.mark.parametrize("call_format", [_responses_call, _chat_completions_call])
@pytest.mark.parametrize("arguments", [{"to": "b@example.com"}, {"cc": "a@example.com"}, {}, "not json", "[1]"])
async def test_executed_call_without_matching_arguments_is_undetermined_not_false_async(
    sqlite_instance: SQLiteMemory, call_format, arguments: object
) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_send_email(call_format, "call_1", arguments))
    await conversation.output_async("call_1")
    reply = await conversation.reply_async()

    score = await _score_arguments(reply, to="a@example.com")

    assert score.status == ScoreStatus.UNDETERMINED
    assert score.score_value is None
    assert "send_email(to)" in score.score_rationale
    # The tool ran, so the rationale must not report that it did not.
    assert "show execution of send_email(to), but no stored call matches the required arguments" in (
        score.score_rationale
    )
    assert "no execution of" not in score.score_rationale


async def test_a_tool_that_never_ran_is_reported_as_not_executed_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    reply = await conversation.reply_async()

    score = await _score_arguments(reply, to="a@example.com")

    assert score.status == ScoreStatus.UNDETERMINED
    assert "Stored messages show no execution of: send_email(to)." in score.score_rationale
    assert "required arguments" not in score.score_rationale


async def test_one_matching_execution_among_several_is_enough_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_send_email(_responses_call, "call_1", {"to": "b@example.com"}))
    await conversation.output_async("call_1")
    await conversation.call_async(_send_email(_responses_call, "call_2", {"to": "a@example.com"}))
    await conversation.output_async("call_2")

    assert (await _score_arguments(await conversation.reply_async(), to="a@example.com")).get_value() is True


async def test_argument_requirements_must_match_within_one_execution_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_send_email(_responses_call, "call_1", {"to": "a@example.com", "subject": "no"}))
    await conversation.output_async("call_1")
    await conversation.call_async(_send_email(_responses_call, "call_2", {"to": "b@example.com", "subject": "hi"}))
    await conversation.output_async("call_2")
    reply = await conversation.reply_async()

    assert (await _score_arguments(reply, to="a@example.com", subject="hi")).status == ScoreStatus.UNDETERMINED
    assert (await _score_arguments(reply, to="b@example.com", subject="hi")).get_value() is True


async def test_arguments_of_a_requested_call_that_did_not_run_are_not_evidence_async(
    sqlite_instance: SQLiteMemory,
) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_send_email(_responses_call, "call_1", {"to": "a@example.com"}))
    await conversation.call_async(_send_email(_responses_call, "call_2", {"to": "b@example.com"}))
    await conversation.output_async("call_2")
    reply = await conversation.reply_async()

    assert (await _score_arguments(reply, to="a@example.com")).status == ScoreStatus.UNDETERMINED
    assert (await _score_arguments(reply, to="b@example.com")).get_value() is True


async def test_tool_names_match_exactly_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("call_1", "Lookup"))
    await conversation.output_async("call_1")
    reply = await conversation.reply_async()

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


async def test_later_turns_are_not_evidence_for_an_earlier_message_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    earlier = await conversation.reply_async()
    await conversation.call_async(_responses_call("call_1", "lookup"))
    await conversation.output_async("call_1")

    assert (await _score(earlier, "lookup")).status == ScoreStatus.UNDETERMINED


async def test_other_conversations_are_not_evidence_async(sqlite_instance: SQLiteMemory) -> None:
    other = await _Conversation.create_async(memory=sqlite_instance)
    await other.call_async(_responses_call("call_1", "lookup"))
    await other.output_async("call_1")
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    for _ in range(3):
        reply = await conversation.reply_async()

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


@pytest.mark.parametrize("role", ["simulated_assistant", "user"])
async def test_calls_the_model_did_not_make_are_ignored_async(sqlite_instance: SQLiteMemory, role: str) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("call_1", "lookup"), role=role)
    await conversation.output_async("call_1")
    reply = await conversation.reply_async()

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


async def test_simulated_tool_output_is_not_execution_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("call_1", "lookup"))
    await conversation.add_async(
        "simulated_tool",
        "function_call_output",
        _output("call_1", "ok"),
        metadata=ToolExecutionMetadata(invoked=True).to_metadata(),
    )
    reply = await conversation.reply_async()

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


async def test_function_call_text_in_a_reply_is_not_a_call_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.add_async("assistant", "text", _responses_call("call_1", "lookup"))
    await conversation.output_async("call_1")
    reply = await conversation.reply_async()

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


async def test_requires_a_tools_called_condition_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    reply = await conversation.reply_async()

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
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("call_1", "lookup"))
    if executed:
        await conversation.output_async("call_1")
    reply = await conversation.reply_async()
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
    # The target runs every call itself and the provider ran no tool, so a call that never ran is a negative.
    [(True, AttackOutcome.SUCCESS), (False, AttackOutcome.FAILURE)],
)
async def test_scores_the_response_target_tool_loop_async(
    registered: bool, outcome: AttackOutcome, sqlite_instance: SQLiteMemory
) -> None:
    target = OpenAIResponseTarget(
        model_name="gpt-4", endpoint="https://mock.azure.com", api_key="mock-key", fail_on_missing_function=False
    )
    if registered:

        async def lookup_async(args: dict) -> dict:
            return {"error": "function_not_found", "missing_function": "lookup", "available_functions": []}

        target._custom_functions["lookup"] = lookup_async
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
    pieces = await sqlite_instance.get_message_pieces_async(conversation_id=result.last_response.conversation_id)
    outputs = [piece for piece in pieces if piece.original_value_data_type == "function_call_output"]
    assert len(outputs) == 1
    assert ToolExecutionMetadata.from_metadata(metadata=outputs[0].prompt_metadata) == ToolExecutionMetadata(
        invoked=registered
    )
    assert json.loads(json.loads(outputs[0].original_value)["output"]) == {
        "error": "function_not_found",
        "missing_function": "lookup",
        "available_functions": [],
    }
    responses = [piece for piece in pieces if piece.role == "assistant"]
    assert len(responses) == 2
    assert all(
        ToolCaptureCoverage.from_metadata(metadata=piece.prompt_metadata) == ToolCaptureCoverage()
        for piece in responses
    )


async def _score_exhaustive_async(message: Message, *names: str) -> Score:
    expectation = ScoringExpectation(
        conditions=(ToolsCalled(tools=tuple(ToolCallRequirement(name=name) for name in names), exhaustive=True),)
    )
    scores = await MessageToolCallScorer().score_async(
        scorable=MessageScorable.from_message(message), expectation=expectation
    )
    assert len(scores) == 1
    return scores[0]


async def test_exhaustive_stored_execution_of_an_unnamed_tool_is_false_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("call_1", "lookup"))
    await conversation.output_async("call_1")
    await conversation.call_async(_responses_call("call_2", "delete"))
    await conversation.output_async("call_2")
    reply = await conversation.reply_async()

    score = await _score_exhaustive_async(reply, "lookup")

    assert score.status == ScoreStatus.COMPLETE
    assert score.get_value() is False
    assert "outside the required set: delete" in score.score_rationale


@pytest.mark.parametrize("names", [("lookup",), ()])
async def test_exhaustive_match_is_never_true_from_stored_messages_async(
    sqlite_instance: SQLiteMemory, names: tuple[str, ...]
) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    if names:
        await conversation.call_async(_responses_call("call_1", "lookup"))
        await conversation.output_async("call_1")
    reply = await conversation.reply_async()

    score = await _score_exhaustive_async(reply, *names)

    assert score.status == ScoreStatus.UNDETERMINED
    assert score.score_value is None


@pytest.mark.parametrize(("extra_tool", "expected"), [(False, None), (True, False)])
async def test_exhaustive_argument_match_stays_open_until_other_tool_evidence_async(
    sqlite_instance: SQLiteMemory, extra_tool: bool, expected: bool | None
) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_send_email(_responses_call, "call_1", {"to": "a@example.com"}))
    await conversation.output_async("call_1")
    if extra_tool:
        await conversation.call_async(_responses_call("call_2", "delete"))
        await conversation.output_async("call_2")
    reply = await conversation.reply_async()
    expectation = ScoringExpectation(
        conditions=(
            ToolsCalled(
                tools=(
                    ToolCallRequirement(
                        name="send_email",
                        arguments=(ToolArgumentRequirement(name="to", matcher=Equals(value="a@example.com")),),
                    ),
                ),
                exhaustive=True,
            ),
        )
    )

    score = (
        await MessageToolCallScorer().score_async(scorable=MessageScorable.from_message(reply), expectation=expectation)
    )[0]

    assert score.score_value == (None if expected is None else str(expected).lower())
    assert score.status == (ScoreStatus.UNDETERMINED if expected is None else ScoreStatus.COMPLETE)
    if extra_tool:
        assert "outside the required set: delete" in score.score_rationale


async def test_exhaustive_ignores_unnamed_calls_that_did_not_run_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _Conversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("call_1", "delete"))
    await conversation.output_async(
        "call_1", {"error": "function_not_found", "missing_function": "delete", "available_functions": []}
    )
    await conversation.call_async(_responses_call("call_2", "send"))
    await conversation.add_async("simulated_assistant", "function_call", _responses_call("call_3", "wipe"))
    await conversation.output_async("call_3")
    reply = await conversation.reply_async()

    score = await _score_exhaustive_async(reply)

    assert score.status == ScoreStatus.UNDETERMINED


# ---------------------------------------------------------------------------
# Complete capture
# ---------------------------------------------------------------------------

_COMPLETE = ToolCaptureCoverage().to_metadata()


class _CapturedConversation(_Conversation):
    """A conversation whose model responses carry the coverage a capturing target records."""

    async def call_async(self, call: dict, *, role: str = "assistant") -> Message:
        return await self.add_async(role, "function_call", call, metadata=dict(_COMPLETE))

    async def reply_async(self, text: str = "done", *, coverage: ToolCaptureCoverage | None = None) -> Message:
        metadata = (coverage or ToolCaptureCoverage()).to_metadata()
        return await self.add_async("assistant", "text", text, metadata=metadata)


def test_capture_coverage_round_trips_through_metadata() -> None:
    coverage = ToolCaptureCoverage(gaps=(ToolCaptureGap.HOSTED_TOOL_CALL, ToolCaptureGap.CALLS_NOT_EXECUTED))
    assert coverage.to_metadata() == {"pyrit_tool_capture": ["hosted_tool_call", "calls_not_executed"]}
    assert ToolCaptureCoverage.from_metadata(metadata=coverage.to_metadata()) == coverage
    assert not coverage.complete
    assert ToolCaptureCoverage().complete
    assert ToolCaptureCoverage.from_metadata(metadata={}) is None


@pytest.mark.parametrize("stored", ["hosted_tool_call", "", {}, None, ["unknown"], [None], {"gaps": []}])
def test_malformed_capture_coverage_is_rejected(stored: object) -> None:
    with pytest.raises(ValueError):
        ToolCaptureCoverage.from_metadata(metadata={ToolCaptureCoverage.METADATA_KEY: stored})


def test_capture_gaps_must_be_unique() -> None:
    with pytest.raises(ValueError, match="unique"):
        ToolCaptureCoverage(gaps=(ToolCaptureGap.HOSTED_TOOL_CALL, ToolCaptureGap.HOSTED_TOOL_CALL))


async def test_complete_capture_without_the_tool_is_false_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _CapturedConversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("call_1", "lookup"))
    await conversation.output_async("call_1")
    reply = await conversation.reply_async()

    score = await _score(reply, "lookup", "delete")

    assert score.status == ScoreStatus.COMPLETE
    assert score.get_value() is False
    assert "no execution of: delete" in score.score_rationale
    assert "rule out other calls" in score.score_rationale


async def test_complete_capture_with_other_arguments_is_false_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _CapturedConversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_send_email(_responses_call, "call_1", {"to": "b@example.com"}))
    await conversation.output_async("call_1")
    reply = await conversation.reply_async()

    score = await _score_arguments(reply, to="a@example.com")

    assert score.get_value() is False
    assert "no stored call matches the required arguments" in score.score_rationale


async def test_complete_capture_still_finds_the_tool_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _CapturedConversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("call_1", "lookup"))
    await conversation.output_async("call_1")
    reply = await conversation.reply_async()

    assert (await _score(reply, "lookup")).get_value() is True


@pytest.mark.parametrize(
    "gap",
    [ToolCaptureGap.HOSTED_TOOL_CALL, ToolCaptureGap.UNRECOGNIZED_OUTPUT, ToolCaptureGap.CALLS_NOT_EXECUTED],
)
async def test_a_recorded_gap_keeps_absence_undetermined_async(
    sqlite_instance: SQLiteMemory, gap: ToolCaptureGap
) -> None:
    conversation = await _CapturedConversation.create_async(memory=sqlite_instance)
    reply = await conversation.reply_async(coverage=ToolCaptureCoverage(gaps=(gap,)))

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED
    assert (await _score_exhaustive_async(reply)).status == ScoreStatus.UNDETERMINED


async def test_one_response_without_coverage_keeps_absence_undetermined_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _CapturedConversation.create_async(memory=sqlite_instance)
    await conversation.add_async("assistant", "text", "an earlier answer from a target that records nothing")
    await conversation.add_async("user", "text", "again")
    reply = await conversation.reply_async()

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


async def test_an_unanswered_request_keeps_absence_undetermined_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _CapturedConversation.create_async(memory=sqlite_instance)
    await conversation.add_async("user", "text", "a send that failed before any response was stored")
    reply = await conversation.reply_async()

    assert (await _score(reply, "lookup")).status == ScoreStatus.UNDETERMINED


async def test_later_unrecorded_turns_do_not_affect_an_earlier_message_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _CapturedConversation.create_async(memory=sqlite_instance)
    reply = await conversation.reply_async()
    await conversation.add_async("user", "text", "next")
    await conversation.add_async("assistant", "text", "unrecorded")

    assert (await _score(reply, "lookup")).get_value() is False


@pytest.mark.parametrize(
    ("names", "calls", "expected"),
    [
        ((), (), True),
        (("lookup",), ("lookup",), True),
        (("lookup",), (), False),
        (("lookup", "delete"), ("lookup",), False),
        (("lookup",), ("lookup", "delete"), False),
    ],
)
async def test_exhaustive_is_decided_from_complete_capture_async(
    sqlite_instance: SQLiteMemory, names: tuple[str, ...], calls: tuple[str, ...], expected: bool
) -> None:
    conversation = await _CapturedConversation.create_async(memory=sqlite_instance)
    for index, name in enumerate(calls):
        await conversation.call_async(_responses_call(f"call_{index}", name))
        await conversation.output_async(f"call_{index}")
    reply = await conversation.reply_async()

    score = await _score_exhaustive_async(reply, *names)

    assert score.status == ScoreStatus.COMPLETE
    assert score.get_value() is expected


async def test_exhaustive_argument_mismatch_is_false_from_complete_capture_async(
    sqlite_instance: SQLiteMemory,
) -> None:
    conversation = await _CapturedConversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_send_email(_responses_call, "call_1", {"to": "b@example.com"}))
    await conversation.output_async("call_1")
    reply = await conversation.reply_async()
    expectation = ScoringExpectation(
        conditions=(
            ToolsCalled(
                tools=(
                    ToolCallRequirement(
                        name="send_email",
                        arguments=(ToolArgumentRequirement(name="to", matcher=Equals(value="a@example.com")),),
                    ),
                ),
                exhaustive=True,
            ),
        )
    )

    score = (
        await MessageToolCallScorer().score_async(scorable=MessageScorable.from_message(reply), expectation=expectation)
    )[0]

    assert score.get_value() is False
    assert "no matching execution of: send_email" in score.score_rationale


def _piece(sequence: int, role: str, *, metadata: dict | None = None) -> MessagePiece:
    piece = MessagePiece(role=role, original_value="x", conversation_id="c", prompt_metadata=metadata or {})
    piece.sequence = sequence
    return piece


@pytest.mark.parametrize(
    ("pieces", "gaps"),
    [
        ([], ("no_messages",)),
        ([_piece(0, "user"), _piece(1, "assistant", metadata=dict(_COMPLETE))], ()),
        ([_piece(0, "user")], ("unanswered_request",)),
        ([_piece(0, "user"), _piece(1, "assistant")], ("capture_not_recorded",)),
        (
            [_piece(0, "user"), _piece(1, "user"), _piece(2, "assistant", metadata=dict(_COMPLETE))],
            ("unanswered_request",),
        ),
        (
            [
                _piece(0, "user", metadata={MessagePiece.PREPENDED_HISTORY_METADATA_KEY: True}),
                _piece(1, "simulated_assistant"),
                _piece(2, "user"),
                _piece(3, "assistant", metadata=dict(_COMPLETE)),
            ],
            (),
        ),
        (
            [
                _piece(0, "user"),
                _piece(
                    1, "assistant", metadata=ToolCaptureCoverage(gaps=(ToolCaptureGap.HOSTED_TOOL_CALL,)).to_metadata()
                ),
                _piece(2, "tool"),
                _piece(3, "assistant"),
            ],
            ("capture_not_recorded", "hosted_tool_call"),
        ),
        (
            [
                _piece(0, "user"),
                _piece(1, "assistant", metadata=dict(_COMPLETE)),
                _piece(2, "tool", metadata={RequestTraceContext.REQUEST_METADATA_KEY: 1}),
            ],
            ("unanswered_request",),
        ),
        ([_piece(0, "user"), _piece(1, "assistant", metadata=dict(_COMPLETE)), _piece(2, "tool")], ()),
    ],
)
def test_capture_gaps_cover_every_request_and_response(pieces: list[MessagePiece], gaps: tuple[str, ...]) -> None:
    assert message_tool_capture_gaps(pieces=pieces) == gaps


# ---------------------------------------------------------------------------
# OpenAIResponseTarget records coverage
# ---------------------------------------------------------------------------


def _sdk_section(section_type: str) -> MagicMock:
    section = MagicMock()
    section.type = section_type
    section.model_dump = MagicMock(return_value={"id": "ws_1", "type": section_type, "status": "completed"})
    return section


def _sdk_with(*extra: MagicMock, text: str = "found it") -> MagicMock:
    response = _sdk_text(text)
    response.output = [*extra, *response.output]
    return response


def _response_target(**kwargs: Any) -> OpenAIResponseTarget:
    return OpenAIResponseTarget(model_name="gpt-4", endpoint="https://mock.azure.com", api_key="mock-key", **kwargs)


async def _send_async(target: OpenAIResponseTarget, responses: list[MagicMock]) -> list[Message]:
    request = Message.from_prompt(prompt="look it up", role="user")
    with patch.object(target._async_client.responses, "create", new_callable=AsyncMock, side_effect=responses):
        return await target.send_prompt_async(message=request)


def _coverage(message: Message) -> ToolCaptureCoverage | None:
    return ToolCaptureCoverage.from_metadata(metadata=message.message_pieces[0].prompt_metadata)


@pytest.mark.parametrize(
    ("sections", "gaps"),
    [
        ((), ()),
        (("reasoning",), ()),
        (("web_search_call",), (ToolCaptureGap.HOSTED_TOOL_CALL,)),
        (("code_interpreter_call", "mcp_call"), (ToolCaptureGap.HOSTED_TOOL_CALL,)),
        (("some_future_call",), (ToolCaptureGap.UNRECOGNIZED_OUTPUT,)),
        (
            ("some_future_call", "file_search_call"),
            (ToolCaptureGap.UNRECOGNIZED_OUTPUT, ToolCaptureGap.HOSTED_TOOL_CALL),
        ),
    ],
)
async def test_response_target_records_unstored_sections_as_gaps_async(
    sections: tuple[str, ...], gaps: tuple[ToolCaptureGap, ...]
) -> None:
    target = _response_target()
    extra = []
    for section_type in sections:
        section = _sdk_section(section_type)
        if section_type == "reasoning":
            section.model_dump = MagicMock(return_value={"type": "reasoning", "summary": []})
        extra.append(section)
    responses = await _send_async(target, [_sdk_with(*extra)])

    assert _coverage(responses[0]) == ToolCaptureCoverage(gaps=gaps)


@pytest.mark.parametrize(("has_call", "gaps"), [(True, (ToolCaptureGap.CALLS_NOT_EXECUTED,)), (False, ())])
async def test_response_target_records_calls_it_did_not_run_async(
    has_call: bool, gaps: tuple[ToolCaptureGap, ...]
) -> None:
    target = _response_target(execute_tools=False)
    response = _sdk_function_call("call_1", "lookup") if has_call else _sdk_text("no tools")
    responses = await _send_async(target, [response])

    assert len(responses) == 1
    assert _coverage(responses[0]) == ToolCaptureCoverage(gaps=gaps)


async def test_tool_loop_stop_records_complete_capture_async() -> None:
    target = _response_target(max_tool_rounds=0)
    responses = await _send_async(target, [_sdk_function_call("call_1", "lookup")])

    assert [message.message_pieces[0].role for message in responses] == ["assistant", "tool", "assistant"]
    assert responses[-1].message_pieces[0].response_error == "processing"
    assert _coverage(responses[0]) == ToolCaptureCoverage()
    assert _coverage(responses[-1]) == ToolCaptureCoverage()


@pytest.mark.parametrize(
    ("hosted", "outcome"),
    [(False, AttackOutcome.FAILURE), (True, AttackOutcome.UNDETERMINED)],
)
async def test_hosted_tools_keep_a_missing_tool_undetermined_through_an_attack_async(
    sqlite_instance: SQLiteMemory, hosted: bool, outcome: AttackOutcome
) -> None:
    target = _response_target()
    attack = PromptSendingAttack(
        objective_target=target,
        attack_scoring_config=AttackScoringConfig(objective_scorer=MessageToolCallScorer()),
        max_attempts_on_failure=0,
    )
    extra = [_sdk_section("web_search_call")] if hosted else []
    if hosted:
        extra[0].model_dump = MagicMock(
            return_value={"id": "ws_1", "type": "web_search_call", "status": "completed", "action": {"type": "search"}}
        )
    with patch.object(
        target._async_client.responses, "create", new_callable=AsyncMock, side_effect=[_sdk_with(*extra)]
    ):
        result = await attack.execute_async(objective="look it up", expectation=_expectation("lookup"))

    assert result.outcome is outcome


# ---------------------------------------------------------------------------
# Coverage cannot be inherited, pending calls block absence, unreadable arguments stay unknown
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fails", [False, True])
async def test_a_request_cannot_pass_coverage_on_to_a_target_that_records_none_async(
    sqlite_instance: SQLiteMemory, fails: bool
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if fails:
            raise httpx.ConnectError("No connection", request=request)
        return httpx.Response(200, text="done")

    target = HTTPTarget(
        http_request="POST / HTTP/1.1\nHost: agent.test\n\n{PROMPT}", transport=httpx.MockTransport(respond)
    )
    request = Message.from_prompt(prompt="look it up", role="user")
    request.get_piece().prompt_metadata.update(_COMPLETE)

    if fails:
        with pytest.raises(Exception, match="Error sending prompt"):
            await PromptNormalizer().send_prompt_async(message=request, target=target)
    else:
        await PromptNormalizer().send_prompt_async(message=request, target=target)

    pieces = await sqlite_instance.get_message_pieces_async(conversation_id=request.get_piece().conversation_id)
    response = next(piece for piece in pieces if piece.role == "assistant")
    assert ToolCaptureCoverage.METADATA_KEY not in response.prompt_metadata
    assert all(ToolCaptureCoverage.METADATA_KEY not in piece.prompt_metadata for piece in pieces)
    score = await _score(response.to_message(), "lookup")
    assert score.status == ScoreStatus.UNDETERMINED


async def test_a_call_without_its_result_in_scope_blocks_absence_verdicts_async(
    sqlite_instance: SQLiteMemory,
) -> None:
    conversation = await _CapturedConversation.create_async(memory=sqlite_instance)
    call = await conversation.call_async(_responses_call("call_1", "lookup"))

    # Scored at the response that asked for the call, before its result exists: the tool may still run.
    assert (await _score(call, "lookup")).status == ScoreStatus.UNDETERMINED
    assert (await _score_exhaustive_async(call)).status == ScoreStatus.UNDETERMINED

    await conversation.output_async("call_1")
    reply = await conversation.reply_async()

    assert (await _score(call, "lookup")).status == ScoreStatus.UNDETERMINED
    assert (await _score(reply, "lookup")).get_value() is True
    assert (await _score_exhaustive_async(reply)).get_value() is False


async def test_a_call_answered_as_not_invoked_counts_as_answered_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _CapturedConversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_responses_call("call_1", "lookup"))
    await conversation.add_async(
        "tool",
        "function_call_output",
        _output("call_1", {"error": "tool_not_executed"}),
        metadata=ToolExecutionMetadata(invoked=False).to_metadata(),
    )
    reply = await conversation.reply_async()

    assert (await _score(reply, "lookup")).get_value() is False
    assert (await _score_exhaustive_async(reply)).get_value() is True


def test_capture_gaps_name_a_call_without_a_result() -> None:
    call = _piece(1, "assistant", metadata=dict(_COMPLETE))
    call.original_value = call.converted_value = json.dumps(_responses_call("call_1", "lookup"))
    call.original_value_data_type = call.converted_value_data_type = "function_call"
    output = _piece(2, "tool")
    output.original_value = output.converted_value = json.dumps(_output("call_1", "ok"))
    output.original_value_data_type = output.converted_value_data_type = "function_call_output"

    assert message_tool_capture_gaps(pieces=[_piece(0, "user"), call]) == ("unanswered_tool_call",)
    assert message_tool_capture_gaps(pieces=[_piece(0, "user"), call, output]) == ()


async def test_unreadable_arguments_stay_unknown_under_complete_capture_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _CapturedConversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_send_email(_responses_call, "call_1", "not json"))
    await conversation.output_async("call_1")
    reply = await conversation.reply_async()

    score = await _score_arguments(reply, to="a@example.com")

    assert score.status == ScoreStatus.UNDETERMINED
    assert "could not be read" in score.score_rationale


async def test_unreadable_arguments_do_not_hide_a_tool_that_never_ran_async(sqlite_instance: SQLiteMemory) -> None:
    conversation = await _CapturedConversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_send_email(_responses_call, "call_1", "not json"))
    await conversation.output_async("call_1")
    reply = await conversation.reply_async()
    expectation = ScoringExpectation(
        conditions=(
            ToolsCalled(
                tools=(
                    ToolCallRequirement(
                        name="send_email",
                        arguments=(ToolArgumentRequirement(name="to", matcher=Equals(value="a@example.com")),),
                    ),
                    ToolCallRequirement(name="lookup"),
                )
            ),
        )
    )

    score = (
        await MessageToolCallScorer().score_async(scorable=MessageScorable.from_message(reply), expectation=expectation)
    )[0]

    assert score.get_value() is False
    assert "no execution of: lookup" in score.score_rationale


@pytest.mark.parametrize(("extra_tool", "expected"), [(False, None), (True, False)])
async def test_exhaustive_keeps_unreadable_arguments_unknown_under_complete_capture_async(
    sqlite_instance: SQLiteMemory, extra_tool: bool, expected: bool | None
) -> None:
    conversation = await _CapturedConversation.create_async(memory=sqlite_instance)
    await conversation.call_async(_send_email(_responses_call, "call_1", "not json"))
    await conversation.output_async("call_1")
    if extra_tool:
        await conversation.call_async(_responses_call("call_2", "delete"))
        await conversation.output_async("call_2")
    reply = await conversation.reply_async()
    expectation = ScoringExpectation(
        conditions=(
            ToolsCalled(
                tools=(
                    ToolCallRequirement(
                        name="send_email",
                        arguments=(ToolArgumentRequirement(name="to", matcher=Equals(value="a@example.com")),),
                    ),
                ),
                exhaustive=True,
            ),
        )
    )

    score = (
        await MessageToolCallScorer().score_async(scorable=MessageScorable.from_message(reply), expectation=expectation)
    )[0]

    assert score.score_value == (None if expected is None else str(expected).lower())
