# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Optional

from pyrit.embedding import OpenAITextEmbedding
from pyrit.memory.memory_models import EmbeddingDataEntry
from pyrit.models import EmbeddingSupport, MessagePiece


class MemoryEmbedding:
    """
    The MemoryEmbedding class is responsible for encoding the memory embeddings.

    Parameters:
        embedding_model (EmbeddingSupport): An instance of a class that supports embedding generation.
    """

    def __init__(self, *, embedding_model: Optional[EmbeddingSupport] = None):
        """
        Initialize the memory embedding helper with a backing embedding model.

        Args:
            embedding_model (Optional[EmbeddingSupport]): The embedding model used to
                generate text embeddings. If not provided, a ValueError is raised.

        Raises:
            ValueError: If `embedding_model` is not provided.
        """
        if embedding_model is None:
            raise ValueError("embedding_model must be set.")
        self.embedding_model = embedding_model

    def generate_embedding_memory_data(self, *, message_piece: MessagePiece) -> EmbeddingDataEntry:
        """
        Generate metadata for a message piece.

        Args:
            message_piece (MessagePiece): the message piece for which to generate a text embedding

        Returns:
            EmbeddingDataEntry: The generated metadata.

        Raises:
            ValueError: If the message piece is not of type text.
        """
        if message_piece.converted_value_data_type == "text":
            embedding_response = self.embedding_model.generate_text_embedding(text=message_piece.converted_value)
            return EmbeddingDataEntry(
                embedding=embedding_response.data[0].embedding,
                embedding_type_name=self.embedding_model.__class__.__name__,
                id=message_piece.id,
            )

        raise ValueError("Only text data is supported for embedding.")


def default_memory_embedding_factory(embedding_model: Optional[EmbeddingSupport] = None) -> MemoryEmbedding | None:
    """
    Create a MemoryEmbedding instance with default or provided embedding model.

    Factory function that creates a MemoryEmbedding instance. If an embedding_model
    is provided, it uses that model. Otherwise, it attempts to create an OpenAI
    embedding model from environment variables.

    Args:
        embedding_model: Optional embedding model to use. If not provided,
            attempts to create OpenAITextEmbedding from environment variables.

    Returns:
        MemoryEmbedding: Configured memory embedding instance.

    Raises:
        ValueError: If no embedding model is provided and required
            OpenAI environment variables are not set.
    """
    if embedding_model:
        return MemoryEmbedding(embedding_model=embedding_model)

    # Try to create OpenAI embedding model from environment variables
    # The constructor will check for OPENAI_EMBEDDING_KEY, OPENAI_EMBEDDING_ENDPOINT, and OPENAI_EMBEDDING_MODEL
    try:
        model = OpenAITextEmbedding()
        return MemoryEmbedding(embedding_model=model)
    except ValueError as e:
        raise ValueError(
            "No embedding model was provided and no OpenAI embedding model was found in the environment."
        ) from e
