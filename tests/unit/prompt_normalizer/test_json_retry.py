# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from unit.mocks import MockPromptTarget

from pyrit.exceptions import InvalidJsonException
from pyrit.models import ConversationRetryReason, Message, MessagePiece
from pyrit.prompt_normalizer import PromptNormalizer, send_json_with_retry_async


class _QueueTarget(MockPromptTarget):
    """A MockPromptTarget that replies with a queued sequence of texts, one per send."""

    def __init__(self, *, responses: list[str]) -> None:
        super().__init__()
        self._responses = list(responses)

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        message = normalized_conversation[-1]
        self.prompt_sent.append(message.get_value())
        text = self._responses.pop(0)
        return [
            MessagePiece(
                role="assistant",
                original_value=text,
                conversation_id=message.message_pieces[0].conversation_id,
            ).to_message()
        ]


def _parse(response: Message) -> dict:
    text = response.get_value()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise InvalidJsonException(message=f"invalid json: {text}") from None


def _user_message() -> Message:
    return Message.from_prompt(prompt="give me json", role="user")


@pytest.mark.usefixtures("patch_central_database")
class TestSendJsonWithRetryAsync:
    async def test_valid_first_attempt_returns_parsed_result(self):
        normalizer = PromptNormalizer()
        target = _QueueTarget(responses=['{"answer": 42}'])
        conversation_id = str(uuid4())

        result = await send_json_with_retry_async(
            normalizer=normalizer,
            target=target,
            message=_user_message(),
            conversation_id=conversation_id,
            parse=_parse,
        )

        assert result == {"answer": 42}
        assert len(target.prompt_sent) == 1
        # No retry occurred, so no retry marker is recorded.
        conversation = normalizer.memory._get_conversation(conversation_id=conversation_id)
        assert conversation is not None
        assert conversation.retries == []

    async def test_retry_then_success_rolls_back_poisoned_turn(self):
        normalizer = PromptNormalizer()
        target = _QueueTarget(responses=["not json at all", '{"answer": 7}'])
        conversation_id = str(uuid4())

        result = await send_json_with_retry_async(
            normalizer=normalizer,
            target=target,
            message=_user_message(),
            conversation_id=conversation_id,
            parse=_parse,
        )

        assert result == {"answer": 7}
        assert len(target.prompt_sent) == 2

        # The malformed first turn must not survive in memory: only the clean retried
        # request/response pair remains, so a caller replaying history never sees the bad reply.
        pieces = normalizer.memory.get_message_pieces(conversation_id=conversation_id)
        values = [piece.original_value for piece in pieces]
        assert "not json at all" not in values
        assert '{"answer": 7}' in values

        # A retry marker was recorded for the rolled-back turn.
        conversation = normalizer.memory._get_conversation(conversation_id=conversation_id)
        assert conversation is not None
        assert len(conversation.retries) == 1
        assert conversation.retries[0].reason == ConversationRetryReason.JSON_PARSING

    async def test_exhausting_retries_raises_invalid_json(self):
        normalizer = PromptNormalizer()
        # conftest sets RETRY_MAX_NUM_ATTEMPTS=2, so two malformed replies exhaust the budget.
        target = _QueueTarget(responses=["nope", "still nope"])
        conversation_id = str(uuid4())

        with pytest.raises(InvalidJsonException):
            await send_json_with_retry_async(
                normalizer=normalizer,
                target=target,
                message=_user_message(),
                conversation_id=conversation_id,
                parse=_parse,
            )

        # Rollback happens at the start of each attempt, so the first failed turn ("nope") is
        # rolled back before the second attempt; a retry marker records it. The final failed turn
        # legitimately remains -- there is no further attempt to poison.
        pieces = normalizer.memory.get_message_pieces(conversation_id=conversation_id)
        assert all(piece.original_value != "nope" for piece in pieces)
        conversation = normalizer.memory._get_conversation(conversation_id=conversation_id)
        assert conversation is not None
        assert len(conversation.retries) == 1

    async def test_no_response_raises_value_error(self):
        normalizer = MagicMock(spec=PromptNormalizer)
        normalizer.memory = MagicMock()
        normalizer.memory.get_message_pieces.return_value = []
        normalizer.memory.delete_conversation_pieces_after_sequence.return_value = 0
        normalizer.send_prompt_async = AsyncMock(return_value=None)
        conversation_id = str(uuid4())

        with pytest.raises(ValueError, match="No response received for conversation ID"):
            await send_json_with_retry_async(
                normalizer=normalizer,
                target=MagicMock(),
                message=_user_message(),
                conversation_id=conversation_id,
                parse=_parse,
            )

    async def test_non_json_exception_propagates_without_retrying(self):
        normalizer = PromptNormalizer()
        target = _QueueTarget(responses=['{"answer": 1}'])
        conversation_id = str(uuid4())

        def _parse_raising(response: Message) -> dict:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await send_json_with_retry_async(
                normalizer=normalizer,
                target=target,
                message=_user_message(),
                conversation_id=conversation_id,
                parse=_parse_raising,
            )

        # A non-JSON error is terminal: the target is hit exactly once (no retry).
        assert len(target.prompt_sent) == 1
