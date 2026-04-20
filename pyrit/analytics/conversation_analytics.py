# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import numpy as np

from pyrit.memory.memory_interface import MemoryInterface
from pyrit.memory.memory_models import (
    ConversationMessageWithSimilarity,
    EmbeddingMessageWithSimilarity,
)


class ConversationAnalytics:
    """
    Handles analytics operations on conversation data, such as finding similar chat messages
    based on conversation history or embedding similarity.
    """

    def __init__(self, *, memory_interface: MemoryInterface):
        """
        Initialize the ConversationAnalytics with a memory interface for data access.

        Args:
            memory_interface (MemoryInterface): An instance of MemoryInterface for accessing conversation data.
        """
        self.memory_interface = memory_interface

    def get_prompt_entries_with_same_converted_content(
        self, *, chat_message_content: str
    ) -> list[ConversationMessageWithSimilarity]:
        """
        Retrieve chat messages that have the same converted content.

        Args:
            chat_message_content (str): The content of the chat message to find similar messages for.

        Returns:
            list[ConversationMessageWithSimilarity]: A list of ConversationMessageWithSimilarity objects representing
            the similar chat messages based on content.
        """
        all_memories = self.memory_interface.get_message_pieces()
        similar_messages = []

        for memory in all_memories:
            if memory.converted_value == chat_message_content:
                similar_messages.append(
                    ConversationMessageWithSimilarity(
                        score=1.0,
                        role=memory.role,
                        content=memory.converted_value,
                        metric="exact_match",  # Exact match
                    )
                )

        return similar_messages

    def get_similar_chat_messages_by_embedding(
        self, *, chat_message_embedding: list[float], threshold: float = 0.8
    ) -> list[EmbeddingMessageWithSimilarity]:
        """
        Retrieve chat messages that are similar to the given embedding based on cosine similarity.

        Args:
            chat_message_embedding (List[float]): The embedding of the chat message to find similar messages for.
            threshold (float): The similarity threshold for considering messages as similar. Defaults to 0.8.

        Returns:
            List[ConversationMessageWithSimilarity]: A list of ConversationMessageWithSimilarity objects representing
            the similar chat messages based on embedding similarity.
        """
        all_embdedding_memory = self.memory_interface.get_all_embeddings()
        similar_messages = []

        target_embedding = np.array(chat_message_embedding).reshape(-1)

        for memory in all_embdedding_memory:
            if not hasattr(memory, "embedding") or memory.embedding is None:
                continue

            memory_embedding = np.array(memory.embedding).reshape(-1)
            similarity_score = cosine_similarity(target_embedding, memory_embedding)

            if similarity_score >= threshold:
                similar_messages.append(
                    EmbeddingMessageWithSimilarity(
                        score=float(similarity_score), uuid=memory.id, metric="cosine_similarity"
                    )
                )

        return similar_messages


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:  # type: ignore[type-arg, unused-ignore]
    """
    Calculate the cosine similarity between two 1D vectors.

    Args:
        a (np.ndarray): The first vector.
        b (np.ndarray): The second vector.

    Returns:
        float: The cosine similarity between the two 1D vectors.

    Raises:
        ValueError: If the input vectors are not 1D.
    """
    # Ensure we are dealing with 1D vectors
    if a.ndim != 1 or b.ndim != 1:
        raise ValueError("Inputs must be 1D vectors")

    dot_product = np.dot(a, b)
    norms = np.linalg.norm(a) * np.linalg.norm(b)

    return float(dot_product / norms)
