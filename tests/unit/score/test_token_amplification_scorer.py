# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid

import pytest

from pyrit.memory import CentralMemory
from pyrit.models import (
    ChatMessageRole,
    Message,
    MessagePiece,
    PromptDataType,
    PromptResponseError,
    TokenUsage,
)
from pyrit.score import FloatScaleThresholdScorer, MessageScorable, TokenAmplificationScorer


def _piece(
    *,
    role: ChatMessageRole,
    conversation_id: str,
    sequence: int,
    output_tokens: int | None = None,
    truncated: bool = False,
    text: str = "text",
    data_type: PromptDataType = "text",
    response_error: PromptResponseError = "none",
) -> MessagePiece:
    """Build a piece, with the token usage a target would save when ``output_tokens`` is set."""
    piece = MessagePiece(
        role=role,
        original_value=text,
        original_value_data_type=data_type,
        response_error=response_error,
        conversation_id=conversation_id,
        sequence=sequence,
        prompt_metadata=TokenUsage(output_tokens=output_tokens).to_metadata() if output_tokens is not None else {},
    )
    if truncated:
        piece.mark_as_truncated()
    return piece


async def _add_conversation_async(
    *,
    output_tokens: list[int | None],
    truncated_replies: tuple[int, ...] = (),
) -> list[Message]:
    """
    Store a conversation of user and assistant turns and return the assistant replies.

    Args:
        output_tokens: The output tokens of each target reply, in order. None saves no token usage.
        truncated_replies: Zero-based indexes of the replies cut at the output limit.

    Returns:
        list[Message]: The stored assistant replies, in conversation order.
    """
    memory = CentralMemory.get_memory_instance()
    conversation_id = str(uuid.uuid4())
    replies: list[Message] = []
    for index, tokens in enumerate(output_tokens):
        user = Message(message_pieces=[_piece(role="user", conversation_id=conversation_id, sequence=index * 2)])
        reply = Message(
            message_pieces=[
                _piece(
                    role="assistant",
                    conversation_id=conversation_id,
                    sequence=index * 2 + 1,
                    output_tokens=tokens,
                    truncated=index in truncated_replies,
                )
            ]
        )
        await memory.add_message_to_memory_async(request=user)
        await memory.add_message_to_memory_async(request=reply)
        replies.append(reply)
    return replies


@pytest.mark.usefixtures("patch_central_database")
class TestTokenAmplificationScorerInit:
    def test_init_default_cap(self):
        scorer = TokenAmplificationScorer()

        assert scorer.get_identifier().params["amplification_cap"] == 10.0

    def test_init_custom_cap_in_identifier(self):
        scorer = TokenAmplificationScorer(amplification_cap=4)

        assert scorer.get_identifier().params["amplification_cap"] == 4.0

    @pytest.mark.parametrize("cap", [1, 0.5, 0, -3, float("inf"), float("nan"), True, "10", None])
    def test_init_invalid_cap_raises(self, cap):
        with pytest.raises(ValueError, match="amplification_cap must be a finite number greater than 1"):
            TokenAmplificationScorer(amplification_cap=cap)


@pytest.mark.usefixtures("patch_central_database")
class TestTokenAmplificationScorerScore:
    async def test_first_reply_scores_ratio_of_one(self):
        replies = await _add_conversation_async(output_tokens=[200])

        [score] = await TokenAmplificationScorer().score_message_async(message=replies[0])

        assert score.score_type == "float_scale"
        assert score.get_value() == pytest.approx(0.1)
        assert score.score_metadata["amplification_ratio"] == pytest.approx(1.0)

    async def test_later_reply_scores_ratio_against_first_reply(self):
        replies = await _add_conversation_async(output_tokens=[200, 350, 600])

        [score] = await TokenAmplificationScorer().score_message_async(message=replies[2])

        assert score.get_value() == pytest.approx(0.3)
        assert score.score_metadata["baseline_output_tokens"] == 200
        assert score.score_metadata["latest_output_tokens"] == 600
        assert score.score_metadata["amplification_ratio"] == pytest.approx(3.0)
        assert score.score_metadata["reply_number"] == 3
        assert score.score_metadata["reply_count"] == 3
        assert score.score_metadata["output_tokens_by_reply"] == "200,350,600"
        assert score.score_metadata["total_output_tokens"] == 1150
        assert score.score_metadata["truncated_replies"] == 0
        assert "3.00 times the first reply" in score.score_rationale

    async def test_middle_reply_uses_its_own_tokens(self):
        replies = await _add_conversation_async(output_tokens=[100, 400, 900])

        [score] = await TokenAmplificationScorer().score_message_async(message=replies[1])

        assert score.get_value() == pytest.approx(0.4)
        assert score.score_metadata["reply_number"] == 2

    async def test_ratio_above_cap_scores_one(self):
        replies = await _add_conversation_async(output_tokens=[100, 5000])

        [score] = await TokenAmplificationScorer(amplification_cap=10).score_message_async(message=replies[1])

        assert score.get_value() == pytest.approx(1.0)
        assert score.score_metadata["amplification_ratio"] == pytest.approx(50.0)

    async def test_smaller_reply_scores_below_first_reply(self):
        replies = await _add_conversation_async(output_tokens=[400, 100])

        [score] = await TokenAmplificationScorer().score_message_async(message=replies[1])

        assert score.get_value() == pytest.approx(0.025)

    async def test_custom_cap_changes_scale(self):
        replies = await _add_conversation_async(output_tokens=[100, 200])

        [score] = await TokenAmplificationScorer(amplification_cap=4).score_message_async(message=replies[1])

        assert score.get_value() == pytest.approx(0.5)

    async def test_truncated_reply_is_reported_as_lower_bound(self):
        replies = await _add_conversation_async(output_tokens=[100, 400], truncated_replies=(1,))

        [score] = await TokenAmplificationScorer().score_message_async(message=replies[1])

        assert score.get_value() == pytest.approx(0.4)
        assert score.score_metadata["truncated_replies"] == 1
        assert "at least this value" in score.score_rationale

    async def test_token_usage_on_first_piece_covers_the_whole_reply(self):
        memory = CentralMemory.get_memory_instance()
        conversation_id = str(uuid.uuid4())
        first = Message(
            message_pieces=[_piece(role="assistant", conversation_id=conversation_id, sequence=1, output_tokens=100)]
        )
        second = Message(
            message_pieces=[
                _piece(role="assistant", conversation_id=conversation_id, sequence=3, output_tokens=300, text="part 1"),
                _piece(role="assistant", conversation_id=conversation_id, sequence=3, text="part 2"),
            ]
        )
        await memory.add_message_to_memory_async(request=first)
        await memory.add_message_to_memory_async(request=second)

        scores = await TokenAmplificationScorer().score_message_async(message=second)

        assert [score.get_value() for score in scores] == [pytest.approx(0.3), pytest.approx(0.3)]

    async def test_simulated_reply_is_not_the_baseline(self):
        memory = CentralMemory.get_memory_instance()
        conversation_id = str(uuid.uuid4())
        simulated = Message(
            message_pieces=[
                _piece(role="simulated_assistant", conversation_id=conversation_id, sequence=1, output_tokens=10)
            ]
        )
        first = Message(
            message_pieces=[_piece(role="assistant", conversation_id=conversation_id, sequence=3, output_tokens=100)]
        )
        second = Message(
            message_pieces=[_piece(role="assistant", conversation_id=conversation_id, sequence=5, output_tokens=200)]
        )
        for message in (simulated, first, second):
            await memory.add_message_to_memory_async(request=message)

        [score] = await TokenAmplificationScorer().score_message_async(message=second)

        assert score.score_metadata["baseline_output_tokens"] == 100
        assert score.score_metadata["reply_count"] == 2
        assert score.get_value() == pytest.approx(0.2)

    async def test_user_message_is_not_scored(self):
        memory = CentralMemory.get_memory_instance()
        user = Message(message_pieces=[_piece(role="user", conversation_id=str(uuid.uuid4()), sequence=0)])
        await memory.add_message_to_memory_async(request=user)

        scores = await TokenAmplificationScorer().score_message_async(message=user)

        assert scores == []


@pytest.mark.usefixtures("patch_central_database")
class TestTokenAmplificationScorerUndetermined:
    async def test_scored_reply_without_token_usage_is_undetermined(self):
        replies = await _add_conversation_async(output_tokens=[100, None])

        [score] = await TokenAmplificationScorer().score_message_async(message=replies[1])

        assert score.is_undetermined
        assert score.score_value is None
        assert "scored reply has no output token count" in score.score_rationale
        assert score.score_metadata["output_tokens_by_reply"] == "100,unknown"

    @pytest.mark.parametrize("first_reply_tokens", [None, 0])
    async def test_first_reply_without_output_tokens_is_undetermined(self, first_reply_tokens):
        replies = await _add_conversation_async(output_tokens=[first_reply_tokens, 500])

        [score] = await TokenAmplificationScorer().score_message_async(message=replies[1])

        assert score.is_undetermined
        assert "first target reply has no output token count" in score.score_rationale

    async def test_response_length_is_never_used_as_an_estimate(self):
        memory = CentralMemory.get_memory_instance()
        conversation_id = str(uuid.uuid4())
        short = Message(message_pieces=[_piece(role="assistant", conversation_id=conversation_id, sequence=1)])
        long = Message(
            message_pieces=[
                _piece(role="assistant", conversation_id=conversation_id, sequence=3, text="long reply " * 500)
            ]
        )
        await memory.add_message_to_memory_async(request=short)
        await memory.add_message_to_memory_async(request=long)

        [score] = await TokenAmplificationScorer().score_message_async(message=long)

        assert score.is_undetermined


@pytest.mark.usefixtures("patch_central_database")
class TestTokenAmplificationScorerWithThreshold:
    @pytest.mark.parametrize(("latest_tokens", "expected"), [(600, True), (599, False), (100, False)])
    async def test_threshold_turns_ratio_into_true_false(self, latest_tokens, expected):
        replies = await _add_conversation_async(output_tokens=[200, latest_tokens])
        scorer = FloatScaleThresholdScorer(scorer=TokenAmplificationScorer(amplification_cap=10), threshold=0.3)

        [score] = await scorer.score_async(scorable=MessageScorable.from_message(replies[1]))

        assert score.score_type == "true_false"
        assert score.get_value() is expected

    async def test_threshold_keeps_missing_token_usage_undetermined(self):
        replies = await _add_conversation_async(output_tokens=[None, None])
        scorer = FloatScaleThresholdScorer(scorer=TokenAmplificationScorer(), threshold=0.3)

        [score] = await scorer.score_async(scorable=MessageScorable.from_message(replies[1]))

        assert score.is_undetermined


async def _add_pieces_async(pieces: list[MessagePiece]) -> list[Message]:
    """Store one message for each piece and return the messages in order."""
    memory = CentralMemory.get_memory_instance()
    messages = [Message(message_pieces=[piece]) for piece in pieces]
    for message in messages:
        await memory.add_message_to_memory_async(request=message)
    return messages


@pytest.mark.usefixtures("patch_central_database")
class TestTokenAmplificationScorerBlockedAndFailedReplies:
    @pytest.mark.parametrize(
        ("data_type", "response_error"), [("error", "blocked"), ("error", "empty"), ("text", "empty")]
    )
    async def test_reply_without_content_is_scored_by_its_token_usage(self, data_type, response_error):
        conversation_id = str(uuid.uuid4())
        messages = await _add_pieces_async(
            [
                _piece(role="user", conversation_id=conversation_id, sequence=0),
                _piece(role="assistant", conversation_id=conversation_id, sequence=1, output_tokens=100),
                _piece(role="user", conversation_id=conversation_id, sequence=2),
                _piece(
                    role="assistant",
                    conversation_id=conversation_id,
                    sequence=3,
                    output_tokens=900,
                    data_type=data_type,
                    response_error=response_error,
                ),
            ]
        )
        scorer = FloatScaleThresholdScorer(scorer=TokenAmplificationScorer(), threshold=0.3)

        [score] = await scorer.score_async(scorable=MessageScorable.from_message(messages[3]))

        assert score.get_value() is True
        assert "9.00 times the first reply" in score.score_rationale

    @pytest.mark.parametrize(
        ("data_type", "response_error"), [("error", "blocked"), ("error", "empty"), ("text", "empty")]
    )
    async def test_reply_without_content_or_token_usage_is_undetermined(self, data_type, response_error):
        conversation_id = str(uuid.uuid4())
        messages = await _add_pieces_async(
            [
                _piece(role="user", conversation_id=conversation_id, sequence=0),
                _piece(role="assistant", conversation_id=conversation_id, sequence=1, output_tokens=100),
                _piece(role="user", conversation_id=conversation_id, sequence=2),
                _piece(
                    role="assistant",
                    conversation_id=conversation_id,
                    sequence=3,
                    data_type=data_type,
                    response_error=response_error,
                ),
            ]
        )
        scorer = FloatScaleThresholdScorer(scorer=TokenAmplificationScorer(), threshold=0.3)

        [score] = await scorer.score_async(scorable=MessageScorable.from_message(messages[3]))

        assert score.is_undetermined
        assert score.score_value is None


@pytest.mark.usefixtures("patch_central_database")
class TestTokenAmplificationScorerToolCalls:
    async def test_tool_call_before_the_first_answer_is_not_the_first_reply(self):
        conversation_id = str(uuid.uuid4())
        messages = await _add_pieces_async(
            [
                _piece(role="user", conversation_id=conversation_id, sequence=0),
                _piece(
                    role="assistant",
                    conversation_id=conversation_id,
                    sequence=1,
                    output_tokens=20,
                    data_type="function_call",
                ),
                _piece(role="tool", conversation_id=conversation_id, sequence=2, data_type="function_call_output"),
                _piece(role="assistant", conversation_id=conversation_id, sequence=3, output_tokens=100),
            ]
        )

        [score] = await TokenAmplificationScorer().score_message_async(message=messages[3])

        assert score.get_value() == pytest.approx(0.1)
        assert score.score_metadata["reply_number"] == 1
        assert score.score_metadata["reply_count"] == 1
        assert score.score_metadata["baseline_output_tokens"] == 100

    async def test_tool_call_between_answers_is_not_counted_as_a_reply(self):
        conversation_id = str(uuid.uuid4())
        messages = await _add_pieces_async(
            [
                _piece(role="user", conversation_id=conversation_id, sequence=0),
                _piece(role="assistant", conversation_id=conversation_id, sequence=1, output_tokens=100),
                _piece(role="user", conversation_id=conversation_id, sequence=2),
                _piece(
                    role="assistant",
                    conversation_id=conversation_id,
                    sequence=3,
                    output_tokens=15,
                    data_type="tool_call",
                ),
                _piece(role="tool", conversation_id=conversation_id, sequence=4, data_type="function_call_output"),
                _piece(role="assistant", conversation_id=conversation_id, sequence=5, output_tokens=400),
            ]
        )

        [score] = await TokenAmplificationScorer().score_message_async(message=messages[5])

        assert score.get_value() == pytest.approx(0.4)
        assert score.score_metadata["reply_number"] == 2
        assert score.score_metadata["output_tokens_by_reply"] == "100,400"
