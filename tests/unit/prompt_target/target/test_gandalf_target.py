# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from unit.mocks import get_image_message_piece

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import GandalfLevel, GandalfTarget


@pytest.fixture
def gandalf_target(patch_central_database) -> GandalfTarget:
    return GandalfTarget(level=GandalfLevel.LEVEL_1)


def test_gandalf_initializes(gandalf_target: GandalfTarget):
    assert gandalf_target


def test_gandalf_sets_endpoint_and_rate_limit():
    target = GandalfTarget(level=GandalfLevel.LEVEL_1, max_requests_per_minute=15)
    identifier = target.get_identifier()
    assert identifier.params["endpoint"] == "https://gandalf-api.lakera.ai/api/send-message"
    assert target._max_requests_per_minute == 15


@pytest.mark.asyncio
async def test_gandalf_validate_request_length(gandalf_target: GandalfTarget):
    request = Message(
        message_pieces=[
            MessagePiece(role="user", conversation_id="123", original_value="test"),
            MessagePiece(role="user", conversation_id="123", original_value="test2"),
        ]
    )
    with pytest.raises(
        ValueError,
        match="This target only supports a single message piece.*If your target does support this, set the"
        " custom_capabilities parameter accordingly",
    ):
        await gandalf_target.send_prompt_async(message=request)


@pytest.mark.asyncio
async def test_gandalf_validate_prompt_type(gandalf_target: GandalfTarget):
    request = Message(message_pieces=[get_image_message_piece()])
    with pytest.raises(
        ValueError,
        match="This target supports only the following data types.*If your target does support this, set the"
        " custom_capabilities parameter accordingly",
    ):
        await gandalf_target.send_prompt_async(message=request)
