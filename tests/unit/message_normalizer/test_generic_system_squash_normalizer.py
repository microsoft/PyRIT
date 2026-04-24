# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.message_normalizer import GenericSystemSquashNormalizer
from pyrit.models import Message, MessagePiece
from pyrit.models.literals import ChatMessageRole


def _make_message(role: ChatMessageRole, content: str) -> Message:
    """Helper to create a Message from role and content."""
    return Message(message_pieces=[MessagePiece(role=role, original_value=content)])


@pytest.mark.asyncio
async def test_generic_squash_system_message():
    messages = [
        _make_message("system", "System message"),
        _make_message("user", "User message 1"),
        _make_message("assistant", "Assistant message"),
    ]
    result = await GenericSystemSquashNormalizer().normalize_async(messages)
    assert len(result) == 2
    assert result[0].api_role == "user"
    assert result[0].get_value() == "### Instructions ###\n\nSystem message\n\n######\n\nUser message 1"
    assert result[1].api_role == "assistant"
    assert result[1].get_value() == "Assistant message"


@pytest.mark.asyncio
async def test_generic_squash_system_message_empty_list():
    with pytest.raises(ValueError):
        await GenericSystemSquashNormalizer().normalize_async(messages=[])


@pytest.mark.asyncio
async def test_generic_squash_system_message_single_system_message():
    messages = [_make_message("system", "System message")]
    result = await GenericSystemSquashNormalizer().normalize_async(messages)
    assert len(result) == 1
    assert result[0].api_role == "user"
    assert result[0].get_value() == "System message"


@pytest.mark.asyncio
async def test_generic_squash_system_message_no_system_message():
    messages = [_make_message("user", "User message 1"), _make_message("user", "User message 2")]
    result = await GenericSystemSquashNormalizer().normalize_async(messages)
    assert len(result) == 2
    assert result[0].api_role == "user"
    assert result[0].get_value() == "User message 1"
    assert result[1].api_role == "user"
    assert result[1].get_value() == "User message 2"


@pytest.mark.asyncio
async def test_generic_squash_normalize_to_dicts_async():
    """Test that normalize_to_dicts_async returns list of dicts with Message.to_dict() format."""
    messages = [
        _make_message("system", "System message"),
        _make_message("user", "User message"),
    ]
    result = await GenericSystemSquashNormalizer().normalize_to_dicts_async(messages)

    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], dict)
    assert result[0]["role"] == "user"
    assert "### Instructions ###" in result[0]["converted_value"]
    assert "System message" in result[0]["converted_value"]
    assert "User message" in result[0]["converted_value"]
