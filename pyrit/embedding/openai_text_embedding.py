# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Optional

import tenacity
from openai import AsyncOpenAI

from pyrit.common import default_values
from pyrit.models import (
    EmbeddingData,
    EmbeddingResponse,
    EmbeddingSupport,
    EmbeddingUsageInformation,
)


class OpenAITextEmbedding(EmbeddingSupport):
    """
    Text embedding class that works with both Azure OpenAI and platform OpenAI endpoints.
    Uses the AsyncOpenAI client under the hood for both providers since they share the same API.
    """

    API_KEY_ENVIRONMENT_VARIABLE: str = "OPENAI_EMBEDDING_KEY"
    ENDPOINT_URI_ENVIRONMENT_VARIABLE: str = "OPENAI_EMBEDDING_ENDPOINT"
    MODEL_ENVIRONMENT_VARIABLE: str = "OPENAI_EMBEDDING_MODEL"

    def __init__(
        self,
        *,
        api_key: Optional[str | Callable[[], str | Awaitable[str]]] = None,
        endpoint: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> None:
        """
        Initialize text embedding client for Azure OpenAI or platform OpenAI.

        Args:
            api_key: The API key (string) or token provider (callable) for authentication.
                For Azure with Entra auth, pass get_azure_openai_auth(endpoint) from pyrit.auth.
                Defaults to OPENAI_EMBEDDING_KEY environment variable.
            endpoint: The API endpoint URL.
                For Azure: https://{resource}.openai.azure.com/openai/v1
                For platform OpenAI: https://api.openai.com/v1
                Defaults to OPENAI_EMBEDDING_ENDPOINT environment variable.
            model_name: The model/deployment name (e.g., "text-embedding-3-small").
                Defaults to OPENAI_EMBEDDING_MODEL environment variable.
        """
        endpoint = default_values.get_required_value(
            env_var_name=self.ENDPOINT_URI_ENVIRONMENT_VARIABLE, passed_value=endpoint
        )
        model_name = default_values.get_required_value(
            env_var_name=self.MODEL_ENVIRONMENT_VARIABLE, passed_value=model_name
        )

        if api_key is None:
            api_key = default_values.get_required_value(
                env_var_name=self.API_KEY_ENVIRONMENT_VARIABLE, passed_value=api_key
            )

        # Create async client - type: ignore needed because get_required_value returns str
        # but api_key parameter accepts str | Callable[[], str | Awaitable[str]]
        self._async_client = AsyncOpenAI(
            api_key=api_key,  # type: ignore[arg-type]
            base_url=endpoint,
        )

        self._model = model_name
        super().__init__()

    @tenacity.retry(wait=tenacity.wait_fixed(0.1), stop=tenacity.stop_after_delay(3))
    async def generate_text_embedding_async(self, text: str, **kwargs: Any) -> EmbeddingResponse:
        """
        Generate text embedding asynchronously.

        Args:
            text: The text to generate the embedding for
            **kwargs: Additional arguments to pass to the embeddings API

        Returns:
            The embedding response
        """
        embedding_obj = await self._async_client.embeddings.create(input=text, model=self._model, **kwargs)
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

    def generate_text_embedding(self, text: str, **kwargs: Any) -> EmbeddingResponse:
        """
        Generate text embedding synchronously by calling the async method.

        Args:
            text: The text to generate the embedding for
            **kwargs: Additional arguments to pass to the embeddings API

        Returns:
            The embedding response
        """
        return asyncio.run(self.generate_text_embedding_async(text=text, **kwargs))
