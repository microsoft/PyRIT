# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from pyrit.exceptions import pyrit_json_retry
from pyrit.models import ConversationRetryReason

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyrit.models import Message
    from pyrit.prompt_normalizer.prompt_normalizer import PromptNormalizer
    from pyrit.prompt_target import PromptTarget

T = TypeVar("T")


async def send_json_with_retry_async(
    *,
    normalizer: PromptNormalizer,
    target: PromptTarget,
    message: Message,
    conversation_id: str,
    parse: Callable[[Message], T],
) -> T:
    """
    Send a message expecting a JSON response, retrying each attempt on a clean conversation history.

    JSON retries are only useful if each attempt is independent. This helper records a baseline
    sequence for the conversation and, on every attempt, rolls memory back to that baseline before
    resending: the first attempt deletes nothing; each retry deletes the previous failed turn (and
    records a ``ConversationRetry`` marker) so the target rebuilds history from memory and sees a
    clean conversation identical to the first attempt, instead of replaying its own malformed reply.

    The retry loop keeps the ``@pyrit_json_retry`` (tenacity) decorator so retry logging and
    the ``RetryCollector`` attribution driven by ``after=log_exception`` are preserved.

    Args:
        normalizer (PromptNormalizer): Normalizer used to send the message. Its memory is the
            source of truth that gets rolled back between attempts.
        target (PromptTarget): The target to send the message to.
        message (Message): The message to send. It is reused across attempts; the normalizer
            deep-copies it, so mutating the persisted copy does not affect this object.
        conversation_id (str): The conversation the message belongs to. Stays stable across
            attempts; only the failed turn's pieces are rolled back.
        parse (Callable[[Message], T]): Turns the response into the parsed result. Must raise
            ``InvalidJsonException`` on a bad parse to trigger a retry. Other exceptions
            (e.g. blocked/empty) propagate without retrying.

    Returns:
        T: The parsed result.

    Raises:
        InvalidJsonException: When parsing still fails after the retry budget is exhausted.
        ValueError: If the target returns no response.
    """
    memory = normalizer.memory
    existing_pieces = memory.get_message_pieces(conversation_id=conversation_id)
    baseline = max((piece.sequence for piece in existing_pieces), default=-1)

    @pyrit_json_retry
    async def _attempt_async() -> T:
        deleted = memory.delete_conversation_pieces_after_sequence(conversation_id=conversation_id, sequence=baseline)
        if deleted:
            memory.add_conversation_retry(
                conversation_id=conversation_id,
                sequence=baseline + 1,
                reason=ConversationRetryReason.JSON_PARSING,
            )
        response = await normalizer.send_prompt_async(message=message, conversation_id=conversation_id, target=target)
        if not response:
            raise ValueError(f"No response received for conversation ID: {conversation_id}")
        return parse(response)

    return await _attempt_async()
