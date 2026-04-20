# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from abc import ABC, abstractmethod
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class EmbeddingUsageInformation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt_tokens: int
    total_tokens: int


class EmbeddingData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    embedding: list[float]
    index: int
    object: str


class EmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    object: str
    usage: EmbeddingUsageInformation
    data: list[EmbeddingData]

    def save_to_file(self, directory_path: Path) -> str:
        """
        Save the embedding response to disk and return the path of the new file.

        Args:
            directory_path: The path to save the file to
        Returns:
            The full path to the file that was saved
        """
        embedding_json = self.model_dump_json()
        embedding_hash = sha256(embedding_json.encode()).hexdigest()
        embedding_output_file_path = Path(directory_path, f"{embedding_hash}.json")
        embedding_output_file_path.write_text(embedding_json)
        return embedding_output_file_path.as_posix()

    @staticmethod
    def load_from_file(file_path: Path) -> EmbeddingResponse:
        """
        Load the embedding response from disk.

        Args:
            file_path: The path to load the file from
        Returns:
            The loaded embedding response
        """
        embedding_json_data = file_path.read_text(encoding="utf-8")
        return EmbeddingResponse.model_validate_json(embedding_json_data)

    def to_json(self) -> str:
        return self.model_dump_json()


class EmbeddingSupport(ABC):
    @abstractmethod
    def generate_text_embedding(self, text: str, **kwargs: object) -> EmbeddingResponse:
        """
        Generate text embedding synchronously.

        Args:
            text: The text to generate the embedding for
            **kwargs: Additional arguments to pass to the function.

        Returns:
            The embedding response
        """
        raise NotImplementedError("generate_text_embedding method not implemented")

    @abstractmethod
    async def generate_text_embedding_async(self, text: str, **kwargs: object) -> EmbeddingResponse:
        """
        Generate text embedding asynchronously.

        Args:
            text: The text to generate the embedding for
            **kwargs: Additional arguments to pass to the function.

        Returns:
            The embedding response
        """
        raise NotImplementedError("generate_text_embedding_async method not implemented")
