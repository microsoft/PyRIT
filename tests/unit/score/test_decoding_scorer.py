# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.analytics import ApproximateTextMatching, ExactTextMatching
from pyrit.converter import AskToDecodeConverter, Base64Converter
from pyrit.executor.attack import AttackConverterConfig, AttackScoringConfig, PromptSendingAttack
from pyrit.memory.central_memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import AttackOutcome, Message, MessagePiece
from pyrit.prompt_normalizer import ConverterConfiguration
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
