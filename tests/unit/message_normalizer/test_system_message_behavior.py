# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import pytest

from pyrit.message_normalizer.message_normalizer import (
    apply_system_message_behavior_async,
)
from pyrit.models import Message, MessagePiece


def _make_message(role: str, content: str) -> Message:
    return Message(message_pieces=[MessagePiece(role=role, original_value=content)])


async def test_apply_system_message_behavior_ignore_removes_system_messages():
    messages = [
        _make_message("system", "You are helpful"),
        _make_message("user", "Hello"),
        _make_message("assistant", "Hi"),
    ]
    result = await apply_system_message_behavior_async(messages, "ignore")
    assert len(result) == 2
    assert all(msg.api_role != "system" for msg in result)


async def test_apply_system_message_behavior_unknown_raises():
    messages = [_make_message("user", "Hello")]
    with pytest.raises(ValueError, match="Unknown system message behavior"):
        await apply_system_message_behavior_async(messages, "bogus")  # type: ignore[arg-type]
