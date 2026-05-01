# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import io
import os
import tempfile
from collections.abc import MutableSequence

import pytest
from unit.mocks import get_sample_conversations

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import TextTarget


@pytest.fixture
def sample_entries() -> MutableSequence[MessagePiece]:
    conversations = get_sample_conversations()
    return Message.flatten_to_message_pieces(conversations)


@pytest.mark.usefixtures("patch_central_database")
def test_init_default_stream_is_stdout():
    import sys

    target = TextTarget()
    assert target._text_stream is sys.stdout


@pytest.mark.usefixtures("patch_central_database")
def test_init_with_custom_stream():
    stream = io.StringIO()
    target = TextTarget(text_stream=stream)
    assert target._text_stream is stream


@pytest.mark.usefixtures("patch_central_database")
async def test_send_prompt_async_writes_to_stream(sample_entries: MutableSequence[MessagePiece]):
    output_stream = io.StringIO()
    target = TextTarget(text_stream=output_stream)

    request = sample_entries[0]
    request.converted_value = "test prompt content"
    await target.send_prompt_async(message=Message(message_pieces=[request]))

    output_stream.seek(0)
    captured = output_stream.read()
    assert "test prompt content" in captured


@pytest.mark.usefixtures("patch_central_database")
async def test_send_prompt_async_returns_empty_list(sample_entries: MutableSequence[MessagePiece]):
    output_stream = io.StringIO()
    target = TextTarget(text_stream=output_stream)

    request = sample_entries[0]
    request.converted_value = "hello"
    result = await target.send_prompt_async(message=Message(message_pieces=[request]))
    assert result == []


@pytest.mark.usefixtures("patch_central_database")
async def test_send_prompt_async_writes_to_file(sample_entries: MutableSequence[MessagePiece]):
    with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".txt") as tmp_file:
        target = TextTarget(text_stream=tmp_file)
        request = sample_entries[0]
        request.converted_value = "file write test"

        await target.send_prompt_async(message=Message(message_pieces=[request]))

        tmp_file.seek(0)
        content = tmp_file.read()

    os.remove(tmp_file.name)
    assert "file write test" in content


@pytest.mark.usefixtures("patch_central_database")
async def test_send_prompt_async_appends_newline(sample_entries: MutableSequence[MessagePiece]):
    output_stream = io.StringIO()
    target = TextTarget(text_stream=output_stream)

    request = sample_entries[0]
    request.converted_value = "prompt text"
    await target.send_prompt_async(message=Message(message_pieces=[request]))

    output_stream.seek(0)
    captured = output_stream.read()
    assert captured.endswith("\n")


@pytest.mark.usefixtures("patch_central_database")
async def test_cleanup_target_does_nothing():
    target = TextTarget(text_stream=io.StringIO())
    # Should not raise
    await target.cleanup_target()
