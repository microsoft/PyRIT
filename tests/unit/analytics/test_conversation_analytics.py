# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Sequence
from unittest.mock import MagicMock

import pytest

from pyrit.analytics.conversation_analytics import ConversationAnalytics
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.memory.memory_models import EmbeddingDataEntry
from pyrit.models import Message, MessagePiece
from unit.mocks import get_sample_conversations


@pytest.fixture
def mock_memory_interface():
    return MagicMock(spec=MemoryInterface)


@pytest.fixture
def sample_message_pieces() -> Sequence[MessagePiece]:
    conversations = get_sample_conversations()
    return Message.flatten_to_message_pieces(conversations)


def test_get_similar_chat_messages_by_content(mock_memory_interface, sample_message_pieces):
    sample_message_pieces[0].converted_value = "Hello, how are you?"
    sample_message_pieces[2].converted_value = "Hello, how are you?"

    mock_memory_interface.get_message_pieces.return_value = sample_message_pieces

    analytics = ConversationAnalytics(memory_interface=mock_memory_interface)
    similar_messages = analytics.get_prompt_entries_with_same_converted_content(
        chat_message_content="Hello, how are you?"
    )

    # Expect one exact match
    assert len(similar_messages) == 2
    for message in similar_messages:
        assert message.content == "Hello, how are you?"
        assert message.score == 1.0
        assert message.metric == "exact_match"


def test_get_similar_chat_messages_by_embedding(mock_memory_interface, sample_message_pieces):
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
    mock_memory_interface.get_all_embeddings.return_value = mock_embeddings
    mock_memory_interface.get_message_pieces.return_value = sample_message_pieces

    analytics = ConversationAnalytics(memory_interface=mock_memory_interface)
    similar_messages = analytics.get_similar_chat_messages_by_embedding(
        chat_message_embedding=target_embedding, threshold=0.99
    )

    # Expect one similar message based on embedding
    assert len(similar_messages) == 1
    assert similar_messages[0].score >= 0.99
    assert similar_messages[0].metric == "cosine_similarity"
