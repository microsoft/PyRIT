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
    await target.cleanup_target_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_cleanup_target_emits_deprecation_warning_and_delegates():
    from unittest.mock import AsyncMock, patch

    target = TextTarget(text_stream=io.StringIO())
    with patch.object(target, "cleanup_target_async", new=AsyncMock()) as mock_async:
        with pytest.warns(DeprecationWarning, match="cleanup_target_async"):
            await target.cleanup_target()
    mock_async.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database")
def test_import_scores_from_csv_emits_deprecation_warning_and_imports():
    target = TextTarget(text_stream=io.StringIO())
    with tempfile.NamedTemporaryFile(mode="w+", delete=False, newline="", suffix=".csv") as tmp_file:
        tmp_file.write("role,value,data_type,conversation_id,sequence,response_error,labels\n")
        tmp_file.write("user,hello,text,conv-1,0,none,{}\n")
        csv_path = tmp_file.name

    try:
        with pytest.warns(DeprecationWarning, match="add_message_pieces_to_memory"):
            message_pieces = target.import_scores_from_csv(csv_file_path=csv_path)
    finally:
        os.remove(csv_path)

    assert len(message_pieces) == 1
    assert message_pieces[0].original_value == "hello"
