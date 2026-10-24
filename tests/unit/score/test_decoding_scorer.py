# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai.types.responses import Response
from unit.mocks import MockPromptTarget, openai_response_json_dict

from pyrit.analytics import ApproximateTextMatching, ExactTextMatching
from pyrit.converter import AskToDecodeConverter, Base64Converter
from pyrit.executor.attack import (
    AttackConverterConfig,
    AttackScoringConfig,
    PrependedConversationConfig,
    PromptSendingAttack,
)
from pyrit.memory.central_memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import AttackOutcome, ChatMessageRole, Message, MessagePiece, RequestTraceContext
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.prompt_target import OpenAIResponseTarget
from pyrit.score import DecodingScorer


@pytest.fixture
def sample_message_pieces():
    """Create sample message pieces for testing."""
    user_piece = MessagePiece(
        role="user",
        original_value="secret",
        converted_value="c2VjcmV0",
        prompt_metadata={"decoded_text": "hidden payload"},
    )

    assistant_piece = MessagePiece(
        role="assistant",
        original_value="Response with secret in it",
        converted_value="Response with secret in it",
    )

    return user_piece, assistant_piece


class _EchoTarget(MockPromptTarget):
    """Answers every request with ``reply``, where ``{received}`` is the text the target received."""

    def __init__(self, *, reply: str) -> None:
        super().__init__()
        self._reply = reply

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        request = normalized_conversation[-1].message_pieces[0]
        return [
            MessagePiece(
                role="assistant",
                original_value=self._reply.format(received=request.converted_value),
                conversation_id=request.conversation_id,
            ).to_message()
        ]


class _ToolCallingTarget(MockPromptTarget):
    """Calls a tool before answering with ``reply``, storing the turn the way ``OpenAIResponseTarget`` does."""

    def __init__(self, *, reply: str) -> None:
        super().__init__()
        self._reply = reply

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        conversation_id = normalized_conversation[-1].message_pieces[0].conversation_id
        return [
            MessagePiece(
                role="assistant",
                original_value=json.dumps({"type": "function_call", "call_id": "call_1", "name": "decode_base64"}),
                original_value_data_type="function_call",
                conversation_id=conversation_id,
            ).to_message(),
            MessagePiece(
                role="tool",
                original_value=json.dumps({"type": "function_call_output", "call_id": "call_1", "output": "secret"}),
                original_value_data_type="function_call_output",
                conversation_id=conversation_id,
            ).to_message(),
            MessagePiece(role="assistant", original_value=self._reply, conversation_id=conversation_id).to_message(),
        ]


class TestDecodingScorer:
    async def test_decoding_scorer_original_value_match(self, patch_central_database, sample_message_pieces):
        user_piece, assistant_piece = sample_message_pieces
        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response_async = AsyncMock(return_value=user_message)

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            scorer = DecodingScorer(categories=["decoding"])
            score = await scorer._score_piece_async(assistant_piece)

            assert len(score) == 1
            assert score[0].get_value() is True
            assert score[0].score_type == "true_false"
            assert score[0].score_category == ["decoding"]
            assert score[0].score_metadata == {
                "text_matcher": "<class 'pyrit.analytics.text_matching.ExactTextMatching'>"
            }

    async def test_decoding_scorer_encoded_echo_does_not_match(self, patch_central_database, sample_message_pieces):
        user_piece, assistant_piece = sample_message_pieces
        assistant_piece.converted_value = "You sent the Base64 string c2VjcmV0"

        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response_async = AsyncMock(return_value=user_message)

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            scorer = DecodingScorer(categories=["decoding"])
            score = await scorer._score_piece_async(assistant_piece)

            assert len(score) == 1
            assert score[0].get_value() is False

    async def test_decoding_scorer_metadata_match(self, patch_central_database, sample_message_pieces):
        user_piece, assistant_piece = sample_message_pieces
        assistant_piece.converted_value = "Response with hidden payload in it"

        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response_async = AsyncMock(return_value=user_message)

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            scorer = DecodingScorer(categories=["decoding"])
            score = await scorer._score_piece_async(assistant_piece)

            assert len(score) == 1
            assert score[0].get_value() is True

    async def test_decoding_scorer_no_match(self, patch_central_database, sample_message_pieces):
        user_piece, assistant_piece = sample_message_pieces
        assistant_piece.converted_value = "Response with nothing matching"

        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response_async = AsyncMock(return_value=user_message)

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            scorer = DecodingScorer(categories=["decoding"])
            score = await scorer._score_piece_async(assistant_piece)

            assert len(score) == 1
            assert score[0].get_value() is False

    async def test_decoding_scorer_empty_converted_value_does_not_match(
        self, patch_central_database, sample_message_pieces
    ):
        user_piece, _ = sample_message_pieces
        user_piece.original_value = "secret"
        user_piece.converted_value = ""

        assistant_piece = MessagePiece(
            role="assistant",
            original_value="I refuse",
            converted_value="I refuse",
        )

        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response_async.return_value = user_message

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            scorer = DecodingScorer(categories=["decoding"])
            score = await scorer._score_piece_async(assistant_piece)

            memory.get_request_from_response_async.assert_awaited_once()
            assert len(score) == 1
            assert score[0].get_value() is False

    async def test_decoding_scorer_case_insensitive(self, patch_central_database, sample_message_pieces):
        user_piece, assistant_piece = sample_message_pieces
        assistant_piece.converted_value = "Response with SECRET in it"

        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response_async = AsyncMock(return_value=user_message)

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            scorer = DecodingScorer(categories=["decoding"])
            score = await scorer._score_piece_async(assistant_piece)

            assert len(score) == 1
            assert score[0].get_value() is True

    async def test_decoding_scorer_case_sensitive(self, patch_central_database, sample_message_pieces):
        user_piece, assistant_piece = sample_message_pieces
        assistant_piece.converted_value = "Response with SECRET in it"

        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response_async = AsyncMock(return_value=user_message)

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            text_matcher = ExactTextMatching(case_sensitive=True)
            scorer = DecodingScorer(text_matcher=text_matcher, categories=["decoding"])
            score = await scorer._score_piece_async(assistant_piece)

            assert len(score) == 1
            assert score[0].get_value() is False

    async def test_decoding_scorer_approximate_matching(self, patch_central_database, sample_message_pieces):
        user_piece, assistant_piece = sample_message_pieces
        assistant_piece.converted_value = "Response with sec ret characters separated"

        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response_async = AsyncMock(return_value=user_message)

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            text_matcher = ApproximateTextMatching(threshold=0.3, n=3, case_sensitive=False)
            scorer = DecodingScorer(text_matcher=text_matcher, categories=["decoding"])
            score = await scorer._score_piece_async(assistant_piece)

            assert len(score) == 1

    async def test_decoding_scorer_approximate_no_match(self, patch_central_database, sample_message_pieces):
        user_piece, assistant_piece = sample_message_pieces
        assistant_piece.converted_value = "Completely different text with no overlap"

        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response_async = AsyncMock(return_value=user_message)

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            text_matcher = ApproximateTextMatching(threshold=0.5, n=4, case_sensitive=False)
            scorer = DecodingScorer(text_matcher=text_matcher, categories=["decoding"])
            score = await scorer._score_piece_async(assistant_piece)

            assert len(score) == 1
            assert score[0].get_value() is False

    @pytest.mark.parametrize(
        "first,second,user_value,response_value",
        [
            (ExactTextMatching(), ExactTextMatching(case_sensitive=True), "Secret", "secret"),
            (ApproximateTextMatching(threshold=0.3), ApproximateTextMatching(threshold=0.9), "secret", "sacret"),
            (ApproximateTextMatching(n=2), ApproximateTextMatching(n=5), "secret", "secrot"),
        ],
    )
    async def test_decoding_identifier_distinguishes_matcher_behavior(
        self, patch_central_database, first, second, user_value, response_value
    ):
        user_piece = MessagePiece(role="user", original_value=user_value, converted_value=user_value)
        assistant_piece = MessagePiece(role="assistant", original_value=response_value, converted_value=response_value)
        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response_async = AsyncMock(return_value=user_message)

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            first_scorer = DecodingScorer(text_matcher=first)
            second_scorer = DecodingScorer(text_matcher=second)

            first_score = await first_scorer._score_piece_async(assistant_piece)
            second_score = await second_scorer._score_piece_async(assistant_piece)

            assert first_score[0].get_value() is not second_score[0].get_value()
            assert first_scorer.get_identifier().hash != second_scorer.get_identifier().hash
            assert first_scorer.get_identifier().eval_hash != second_scorer.get_identifier().eval_hash

    @pytest.mark.parametrize("matcher_class", [ExactTextMatching, ApproximateTextMatching])
    def test_decoding_identifier_stable_for_equivalent_matchers(self, patch_central_database, matcher_class):
        first = DecodingScorer(text_matcher=matcher_class())
        second = DecodingScorer(text_matcher=matcher_class())

        assert first.get_identifier().hash == second.get_identifier().hash
        assert first.get_identifier().eval_hash == second.get_identifier().eval_hash

    @pytest.mark.parametrize(
        "decode_template,reply,expected_outcome",
        [
            pytest.param(None, "You sent: {received}", AttackOutcome.FAILURE, id="raw-echo"),
            pytest.param(
                AskToDecodeConverter.garak_templates[0], "You sent: {received}", AttackOutcome.FAILURE, id="decode-echo"
            ),
            pytest.param(None, "Decoded: secret", AttackOutcome.SUCCESS, id="plaintext"),
            pytest.param(None, "I refuse", AttackOutcome.FAILURE, id="refusal"),
        ],
    )
    async def test_encoding_attack_counts_only_decoded_replies(
        self, patch_central_database, decode_template, reply, expected_outcome
    ):
        converters = [Base64Converter()]
        if decode_template:
            converters.append(AskToDecodeConverter(template=decode_template, encoding_name="base64"))
        attack = PromptSendingAttack(
            objective_target=_EchoTarget(reply=reply),
            attack_converter_config=AttackConverterConfig(
                request_converters=ConverterConfiguration.from_converters(converters=converters)
            ),
            attack_scoring_config=AttackScoringConfig(objective_scorer=DecodingScorer()),
        )

        result = await attack.execute_async(objective="secret")

        assert result.outcome == expected_outcome

    @pytest.mark.parametrize(
        "reply,expected_outcome",
        [
            pytest.param("Decoded: secret", AttackOutcome.SUCCESS, id="plaintext"),
            pytest.param("I refuse", AttackOutcome.FAILURE, id="refusal"),
        ],
    )
    async def test_encoding_attack_scores_reply_after_tool_call(self, patch_central_database, reply, expected_outcome):
        attack = PromptSendingAttack(
            objective_target=_ToolCallingTarget(reply=reply),
            attack_converter_config=AttackConverterConfig(
                request_converters=ConverterConfiguration.from_converters(converters=[Base64Converter()])
            ),
            attack_scoring_config=AttackScoringConfig(objective_scorer=DecodingScorer()),
        )

        result = await attack.execute_async(objective="secret")

        assert result.outcome == expected_outcome


def _sdk_response(*, output: list[dict[str, Any]]) -> Response:
    return Response.model_validate(
        {
            **openai_response_json_dict(),
            "created_at": 1,
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "output": output,
        }
    )


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("request_role", ["user", "system", "developer", "assistant", "tool"])
@pytest.mark.parametrize("tool_rounds", [(), (1,), (1, 1), (2,)], ids=["plain", "single", "chained", "parallel"])
@pytest.mark.parametrize(
    "reply,expected_outcome",
    [
        pytest.param("Decoded: secret", AttackOutcome.SUCCESS, id="plaintext"),
        pytest.param("I refuse", AttackOutcome.FAILURE, id="refusal"),
        pytest.param("Earlier request", AttackOutcome.FAILURE, id="earlier-request-echo"),
    ],
)
async def test_encoding_attack_scores_explicit_request_async(
    *,
    sqlite_instance: MemoryInterface,
    request_role: ChatMessageRole,
    tool_rounds: tuple[int, ...],
    reply: str,
    expected_outcome: AttackOutcome,
) -> None:
    async def decode_async(arguments: dict[str, Any]) -> str:
        return "secret"

    target = OpenAIResponseTarget(
        model_name="test-model",
        endpoint="https://mock.azure.com/",
        api_key="mock-key",
        custom_functions={"decode": decode_async},
    )
    history = [
        Message.from_prompt(prompt="Earlier request", role="user"),
        Message.from_prompt(prompt="Earlier answer", role="assistant"),
    ]
    request = Message.from_prompt(prompt="secret", role=request_role)
    converters = ConverterConfiguration.from_converters(converters=[Base64Converter()])
    if request_role == "tool":
        history[-1] = MessagePiece(
            role="assistant",
            original_value=json.dumps(
                {"type": "function_call", "call_id": "prior_call", "name": "lookup", "arguments": "{}"}
            ),
            original_value_data_type="function_call",
        ).to_message()
        request.get_piece().converted_value = json.dumps(
            {"type": "function_call_output", "call_id": "prior_call", "output": "c2VjcmV0"}
        )
        request.get_piece().converted_value_data_type = "function_call_output"
        converters = []
    responses = []
    for round_index, call_count in enumerate(tool_rounds):
        responses.append(
            _sdk_response(
                output=[
                    {
                        "id": f"fc_{round_index}_{call_index}",
                        "type": "function_call",
                        "call_id": f"call_{round_index}_{call_index}",
                        "name": "decode",
                        "arguments": "{}",
                        "status": "completed",
                    }
                    for call_index in range(call_count)
                ]
            )
        )
    responses.append(
        _sdk_response(
            output=[
                {
                    "id": "msg_reply",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": reply, "annotations": []}],
                }
            ]
        )
    )
    attack = PromptSendingAttack(
        objective_target=target,
        attack_converter_config=AttackConverterConfig(request_converters=converters),
        attack_scoring_config=AttackScoringConfig(objective_scorer=DecodingScorer(categories=["decoding"])),
        prepended_conversation_config=PrependedConversationConfig(apply_converters_to_roles=[]),
    )
    try:
        with patch.object(
            target._client.responses, "create", new_callable=AsyncMock, side_effect=responses
        ) as create_response:
            result = await attack.execute_async(
                objective="secret", next_message=request, prepended_conversation=history
            )

        assert create_response.await_count == len(responses)
        assert result.outcome == expected_outcome
        assert result.last_response is not None
        stored_request = await sqlite_instance.get_request_from_response_async(
            response=result.last_response.to_message()
        )
        assert stored_request.api_role == request_role
        assert stored_request.get_piece().original_value == "secret"
        assert stored_request.get_piece().prompt_metadata[RequestTraceContext.REQUEST_METADATA_KEY] == 1
        assert result.last_score is not None
        scores = await sqlite_instance.get_scores_async(score_ids=[str(result.last_score.id)])
        assert len(scores) == 1
        assert scores[0].get_value() is (expected_outcome == AttackOutcome.SUCCESS)
        assert scores[0].score_category == ["decoding"]
        assert scores[0].score_rationale == ""
        assert scores[0].score_metadata == {"text_matcher": "<class 'pyrit.analytics.text_matching.ExactTextMatching'>"}
    finally:
        await target._client.close()
