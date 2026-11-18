# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field


class ReasoningContentMetadata(BaseModel):
    """Provider reasoning retained separately from the answer and outbound history."""

    METADATA_KEY: ClassVar[str] = "pyrit_reasoning_content"
    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str = Field(strict=True, min_length=1)

    def to_metadata(self) -> dict[str, Any]:
        """Return the response metadata fragment containing the reasoning."""
        return {self.METADATA_KEY: self.model_dump(mode="json")}

    @classmethod
    def from_metadata(cls, *, metadata: dict[str, Any]) -> Self | None:
        """
        Read reasoning, treating absent or legacy unnamespaced values as unset.

        Args:
            metadata (dict[str, Any]): The response piece's metadata.

        Returns:
            Self | None: The recorded reasoning, or None when absent.

        Raises:
            ValueError: If the namespaced reasoning metadata is malformed.
        """
        if cls.METADATA_KEY not in metadata:
            return None
        return cls.model_validate(metadata[cls.METADATA_KEY])

    @classmethod
    def clear_metadata(cls, *, metadata: dict[str, Any]) -> None:
        """
        Remove inherited reasoning before recording a new response.

        Args:
            metadata (dict[str, Any]): The response piece's metadata to update.
        """
        metadata.pop(cls.METADATA_KEY, None)
        metadata.pop("reasoning_content", None)
