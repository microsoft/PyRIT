# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
from typing import Any, Union

import tenacity
from openai import AzureOpenAI, OpenAI

from pyrit.models import (
    EmbeddingData,
    EmbeddingResponse,
    EmbeddingSupport,
    EmbeddingUsageInformation,
)


class _TextEmbedding(EmbeddingSupport, abc.ABC):
    """Text embedding base class."""

    _client: Union[OpenAI, AzureOpenAI]
    _model: str

    def __init__(self) -> None:
        super().__init__()
        if not (hasattr(self, "_client") and hasattr(self, "_model")):
            raise NotImplementedError(
                "Text embedding client and model need to be provided by the implementing child class."
            )

    @tenacity.retry(wait=tenacity.wait_fixed(0.1), stop=tenacity.stop_after_delay(3))
    def generate_text_embedding(self, text: str, **kwargs: Any) -> EmbeddingResponse:
        """
        Generate text embedding.

        Args:
            text: The text to generate the embedding for
            **kwargs: Additional arguments to pass to the LLM client API

        Returns:
            The embedding response
        """
        embedding_obj = self._client.embeddings.create(input=text, model=self._model, **kwargs)
        return EmbeddingResponse(
            model=embedding_obj.model,
            object=embedding_obj.object,
            data=[
                EmbeddingData(
                    embedding=embedding_obj.data[0].embedding,
                    index=embedding_obj.data[0].index,
                    object=embedding_obj.data[0].object,
                )
            ],
            usage=EmbeddingUsageInformation(
                prompt_tokens=embedding_obj.usage.prompt_tokens,
                total_tokens=embedding_obj.usage.total_tokens,
            ),
        )
