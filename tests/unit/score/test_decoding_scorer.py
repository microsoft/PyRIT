# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

from pyrit.analytics import ApproximateTextMatching, ExactTextMatching
from pyrit.memory.central_memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import Message, MessagePiece
from pyrit.score import DecodingScorer


@pytest.fixture
def sample_message_pieces():
    """Create sample message pieces for testing."""
    user_piece = MessagePiece(
        role="user",
        original_value="secret",
        converted_value="encoded_secret",
        prompt_metadata={"decoded_text": "decoded_secret"},
    )

    assistant_piece = MessagePiece(
        role="assistant",
        original_value="Response with secret in it",
        converted_value="Response with secret in it",
    )

    return user_piece, assistant_piece


class TestDecodingScorer:
    @pytest.mark.asyncio
    async def test_decoding_scorer_original_value_match(self, patch_central_database, sample_message_pieces):
        user_piece, assistant_piece = sample_message_pieces

        # Mock memory to return the user request
        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response.return_value = user_message

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            scorer = DecodingScorer(categories=["decoding"])
            score = await scorer._score_piece_async(assistant_piece)

            assert len(score) == 1
            assert score[0].get_value() is True  # "secret" is in the response
            assert score[0].score_type == "true_false"
            assert score[0].score_category == ["decoding"]

    @pytest.mark.asyncio
    async def test_decoding_scorer_converted_value_match(self, patch_central_database, sample_message_pieces):
        user_piece, assistant_piece = sample_message_pieces
        assistant_piece.converted_value = "Response with encoded_secret in it"

        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response.return_value = user_message

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            scorer = DecodingScorer(categories=["decoding"])
            score = await scorer._score_piece_async(assistant_piece)

            assert len(score) == 1
            assert score[0].get_value() is True  # "encoded_secret" is in the response

    @pytest.mark.asyncio
    async def test_decoding_scorer_metadata_match(self, patch_central_database, sample_message_pieces):
        user_piece, assistant_piece = sample_message_pieces
        assistant_piece.converted_value = "Response with decoded_secret in it"

        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response.return_value = user_message

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            scorer = DecodingScorer(categories=["decoding"])
            score = await scorer._score_piece_async(assistant_piece)

            assert len(score) == 1
            assert score[0].get_value() is True  # "decoded_secret" from metadata is in response

    @pytest.mark.asyncio
    async def test_decoding_scorer_no_match(self, patch_central_database, sample_message_pieces):
        user_piece, assistant_piece = sample_message_pieces
        assistant_piece.converted_value = "Response with nothing matching"

        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response.return_value = user_message

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            scorer = DecodingScorer(categories=["decoding"])
            score = await scorer._score_piece_async(assistant_piece)

            assert len(score) == 1
            assert score[0].get_value() is False

    @pytest.mark.asyncio
    async def test_decoding_scorer_case_insensitive(self, patch_central_database, sample_message_pieces):
        user_piece, assistant_piece = sample_message_pieces
        assistant_piece.converted_value = "Response with SECRET in it"

        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response.return_value = user_message

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            # Default is case insensitive
            scorer = DecodingScorer(categories=["decoding"])
            score = await scorer._score_piece_async(assistant_piece)

            assert len(score) == 1
            assert score[0].get_value() is True  # Case insensitive match

    @pytest.mark.asyncio
    async def test_decoding_scorer_case_sensitive(self, patch_central_database, sample_message_pieces):
        user_piece, assistant_piece = sample_message_pieces
        assistant_piece.converted_value = "Response with SECRET in it"

        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response.return_value = user_message

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            text_matcher = ExactTextMatching(case_sensitive=True)
            scorer = DecodingScorer(text_matcher=text_matcher, categories=["decoding"])
            score = await scorer._score_piece_async(assistant_piece)

            assert len(score) == 1
            assert score[0].get_value() is False  # Case sensitive, no match

    @pytest.mark.asyncio
    async def test_decoding_scorer_approximate_matching(self, patch_central_database, sample_message_pieces):
        user_piece, assistant_piece = sample_message_pieces
        # Partial match - has some n-grams in common
        assistant_piece.converted_value = "Response with sec ret characters separated"

        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response.return_value = user_message

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            # Use approximate matching with low threshold
            text_matcher = ApproximateTextMatching(threshold=0.3, n=3, case_sensitive=False)
            scorer = DecodingScorer(text_matcher=text_matcher, categories=["decoding"])
            score = await scorer._score_piece_async(assistant_piece)

            assert len(score) == 1
            # Should detect partial match with low threshold

    @pytest.mark.asyncio
    async def test_decoding_scorer_approximate_no_match(self, patch_central_database, sample_message_pieces):
        user_piece, assistant_piece = sample_message_pieces
        assistant_piece.converted_value = "Completely different text with no overlap"

        memory = MagicMock(MemoryInterface)
        user_message = Message(message_pieces=[user_piece])
        memory.get_request_from_response.return_value = user_message

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            text_matcher = ApproximateTextMatching(threshold=0.5, n=4, case_sensitive=False)
            scorer = DecodingScorer(text_matcher=text_matcher, categories=["decoding"])
            score = await scorer._score_piece_async(assistant_piece)

            assert len(score) == 1
            assert score[0].get_value() is False  # Below threshold
