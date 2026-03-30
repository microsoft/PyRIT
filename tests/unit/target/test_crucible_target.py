# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from unit.mocks import get_image_message_piece

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import CrucibleTarget


@pytest.fixture
def crucible_target(patch_central_database) -> CrucibleTarget:
    return CrucibleTarget(endpoint="https://crucible", api_key="abc")


def test_crucible_initializes(crucible_target: CrucibleTarget):
    assert crucible_target


def test_crucible_sets_endpoint_and_rate_limit():
    target = CrucibleTarget(endpoint="https://crucible", api_key="abc", max_requests_per_minute=10)
    identifier = target.get_identifier()
    assert identifier.params["endpoint"] == "https://crucible"
    assert target._max_requests_per_minute == 10


@pytest.mark.asyncio
async def test_crucible_validate_request_length(crucible_target: CrucibleTarget):
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
        await crucible_target.send_prompt_async(message=request)


@pytest.mark.asyncio
async def test_crucible_validate_prompt_type(crucible_target: CrucibleTarget):
    message_piece = get_image_message_piece()
    request = Message(message_pieces=[message_piece])
    with pytest.raises(
        ValueError,
        match="This target supports only the following data types.*If your target does support this, set the"
        " custom_capabilities parameter accordingly",
    ):
        await crucible_target.send_prompt_async(message=request)
