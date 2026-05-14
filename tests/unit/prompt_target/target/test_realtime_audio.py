# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio

from pyrit.prompt_target.common.realtime_audio import _RealtimeTurnState


async def test_realtime_turn_state_defaults():
    """Newly constructed turn state must be empty: no audio, no transcripts, not responding, not interrupted."""
    state = _RealtimeTurnState(completion=asyncio.get_event_loop().create_future())

    assert state.is_responding is False
    assert state.interrupted is False
    assert bytes(state.delivered_audio) == b""
    assert state.delivered_transcripts == []
    assert state.current_item_id is None
    assert state.last_response_id is None
