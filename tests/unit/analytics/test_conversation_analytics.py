# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Sequence
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from pyrit.analytics.conversation_analytics import ConversationAnalytics, cosine_similarity
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.memory.memory_models import EmbeddingDataEntry
from pyrit.models import MessagePiece, flatten_to_message_pieces
from unit.mocks import get_sample_conversations


@pytest.fixture
def mock_memory_interface():
    return MagicMock(spec=MemoryInterface)


@pytest.fixture
def sample_message_pieces() -> Sequence[MessagePiece]:
    conversations = get_sample_conversations()
    return flatten_to_message_pieces(conversations)


async def test_get_similar_chat_messages_by_content(mock_memory_interface, sample_message_pieces):
    sample_message_pieces[0].converted_value = "Hello, how are you?"
    sample_message_pieces[2].converted_value = "Hello, how are you?"

    mock_memory_interface.get_message_pieces_async = AsyncMock(return_value=sample_message_pieces)

    analytics = ConversationAnalytics(memory_interface=mock_memory_interface)
    similar_messages = await analytics.get_prompt_entries_with_same_converted_content_async(
        chat_message_content="Hello, how are you?"
    )

    # Expect one exact match
    assert len(similar_messages) == 2
    for message in similar_messages:
        assert message.content == "Hello, how are you?"
        assert message.score == 1.0
        assert message.metric == "exact_match"


async def test_get_similar_chat_messages_by_embedding(mock_memory_interface, sample_message_pieces):
    sample_message_pieces[0].converted_value = "Similar message"
    sample_message_pieces[1].converted_value = "Different message"

    # Mock EmbeddingData entries linked to the ConversationData entries
    target_embedding = [0.1, 0.2, 0.3]
    similar_embedding = [0.1, 0.2, 0.31]  # Slightly different, but should be similar
    different_embedding = [0.9, 0.8, 0.7]

    mock_embeddings = [
        EmbeddingDataEntry(id=sample_message_pieces[0].id, embedding=similar_embedding, embedding_type_name="model1"),
        EmbeddingDataEntry(id=sample_message_pieces[1].id, embedding=different_embedding, embedding_type_name="model2"),
    ]

    # Mock the get_all_embeddings method to return the mock EmbeddingData entries
    mock_memory_interface.get_all_embeddings_async = AsyncMock(return_value=mock_embeddings)
    mock_memory_interface.get_message_pieces_async = AsyncMock(return_value=sample_message_pieces)

    analytics = ConversationAnalytics(memory_interface=mock_memory_interface)
    similar_messages = await analytics.get_similar_chat_messages_by_embedding_async(
        chat_message_embedding=target_embedding, threshold=0.99
    )

    # Expect one similar message based on embedding
    assert len(similar_messages) == 1
    assert similar_messages[0].score >= 0.99
    assert similar_messages[0].metric == "cosine_similarity"


async def test_embedding_search_skips_missing_vectors_and_includes_threshold_boundary(
    mock_memory_interface: MagicMock, sample_message_pieces: Sequence[MessagePiece]
) -> None:
    matching = EmbeddingDataEntry(id=sample_message_pieces[0].id, embedding=[1.0, 0.0], embedding_type_name="test")
    missing = EmbeddingDataEntry(id=sample_message_pieces[1].id, embedding=None, embedding_type_name="test")
    orthogonal = EmbeddingDataEntry(id=sample_message_pieces[2].id, embedding=[0.0, 1.0], embedding_type_name="test")
    mock_memory_interface.get_all_embeddings_async.return_value = [missing, matching, orthogonal]
    mock_memory_interface.get_all_embeddings.return_value = [missing, matching, orthogonal]
    analytics = ConversationAnalytics(memory_interface=mock_memory_interface)

    actual = await analytics.get_similar_chat_messages_by_embedding_async(
        chat_message_embedding=[1.0, 0.0], threshold=1.0
    )
    with pytest.warns(DeprecationWarning, match="get_similar_chat_messages_by_embedding"):
        legacy = analytics.get_similar_chat_messages_by_embedding(chat_message_embedding=[1.0, 0.0], threshold=1.0)
    for matches in (actual, legacy):
        assert len(matches) == 1
        assert matches[0].uuid == matching.id
        assert matches[0].score == 1.0
        assert matches[0].metric == "cosine_similarity"
    mock_memory_interface.get_all_embeddings_async.assert_awaited_once()


@pytest.mark.parametrize(
    "a,b",
    [
        (np.array([0.0, 0.0]), np.array([1.0, 2.0])),
        (np.array([1.0, 2.0]), np.array([0.0, 0.0])),
        (np.array([0.0, 0.0]), np.array([0.0, 0.0])),
    ],
)
def test_cosine_similarity_zero_vector_returns_zero(a: np.ndarray, b: np.ndarray) -> None:
    assert cosine_similarity(a, b) == 0.0
