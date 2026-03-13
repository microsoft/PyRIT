# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
from typing import Any, Generic, Literal, Protocol, TypeVar

from pyrit.models import Message

# Type alias for system message handling strategies
SystemMessageBehavior = Literal["keep", "squash", "ignore"]
"""
How to handle system messages in models with varying support:
- "keep": Keep system messages as-is (default for most models)
- "squash": Merge system message into first user message
- "ignore": Drop system messages entirely
"""


class DictConvertible(Protocol):
    """Protocol for objects that can be converted to a dictionary."""

    def to_dict(self) -> dict[str, Any]:
        """Convert the object to a dictionary representation."""
        ...


T = TypeVar("T", bound=DictConvertible)


class MessageListNormalizer(abc.ABC, Generic[T]):
    """
    Abstract base class for normalizers that return a list of items.

    Subclasses specify the type T (e.g., Message, ChatMessage) that the list contains.
    T must implement the DictConvertible protocol (have a to_dict() method).
    """

    @abc.abstractmethod
    async def normalize_async(self, messages: list[Message]) -> list[T]:
        """
        Normalize the list of messages into a list of items.

        Args:
            messages: The list of Message objects to normalize.

        Returns:
            A list of normalized items of type T.
        """

    async def normalize_to_dicts_async(self, messages: list[Message]) -> list[dict[str, Any]]:
        """
        Normalize the list of messages into a list of dictionaries.

        This method uses normalize_async and calls to_dict() on each item.

        Args:
            messages: The list of Message objects to normalize.

        Returns:
            A list of dictionaries representing the normalized messages.
        """
        normalized = await self.normalize_async(messages)
        return [item.to_dict() for item in normalized]


class MessageStringNormalizer(abc.ABC):
    """
    Abstract base class for normalizers that return a string representation.

    Use this for formatting messages into text for non-chat targets or context strings.
    """

    @abc.abstractmethod
    async def normalize_string_async(self, messages: list[Message]) -> str:
        """
        Normalize the list of messages into a string representation.

        Args:
            messages: The list of Message objects to normalize.

        Returns:
            A string representation of the messages.
        """


async def apply_system_message_behavior(messages: list[Message], behavior: SystemMessageBehavior) -> list[Message]:
    """
    Apply a system message behavior to a list of messages.

    This is a helper function used by normalizers to preprocess messages
    based on how the target handles system messages.

    Args:
        messages: The list of Message objects to process.
        behavior: How to handle system messages:
            - "keep": Return messages unchanged
            - "squash": Merge system into first user message
            - "ignore": Remove system messages

    Returns:
        The processed list of Message objects.

    Raises:
        ValueError: If an unknown behavior is provided.
    """
    if behavior == "keep":
        return messages
    if behavior == "squash":
        # Import here to avoid circular imports
        from pyrit.message_normalizer.generic_system_squash import (
            GenericSystemSquashNormalizer,
        )

        return await GenericSystemSquashNormalizer().normalize_async(messages)
    if behavior == "ignore":
        return [msg for msg in messages if msg.role != "system"]
    # This should never happen due to Literal type, but handle it gracefully
    raise ValueError(f"Unknown system message behavior: {behavior}")
