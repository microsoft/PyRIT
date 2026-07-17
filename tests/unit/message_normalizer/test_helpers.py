# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.message_normalizer._helpers import build_squashed_user_message
from pyrit.models import Message, MessagePiece


def _message(*, role: str, content: str, metadata: dict | None = None) -> Message:
    return Message(
        message_pieces=[
            MessagePiece(role=role, original_value=content, prompt_metadata=metadata or {}),
        ]
    )


def test_build_squashed_user_message_propagates_last_metadata():
    metadata = {"json_schema": {"type": "object"}, "scenario": "x"}
    messages = [
        _message(role="assistant", content="earlier", metadata={"unused": "ignored"}),
        _message(role="user", content="current", metadata=metadata),
    ]

    squashed = build_squashed_user_message(new_message_content="combined text", source_messages=messages)

    piece = squashed.message_pieces[0]
    assert squashed.api_role == "user"
    assert piece.converted_value == "combined text"
    assert piece.prompt_metadata == metadata


def test_build_squashed_user_message_single_source_uses_its_metadata():
    metadata = {"json_schema": {"type": "object"}}
    messages = [_message(role="system", content="sys", metadata=metadata)]

    squashed = build_squashed_user_message(new_message_content="sys", source_messages=messages)

    piece = squashed.message_pieces[0]
    assert squashed.api_role == "user"
    assert piece.prompt_metadata == metadata


def test_build_squashed_user_message_empty_metadata_is_ok():
    messages = [_message(role="user", content="hi")]
    squashed = build_squashed_user_message(new_message_content="hi", source_messages=messages)
    assert squashed.message_pieces[0].prompt_metadata == {}


def test_build_squashed_user_message_empty_sources_raises():
    with pytest.raises(ValueError, match="source_messages"):
        build_squashed_user_message(new_message_content="anything", source_messages=[])


def test_build_squashed_user_message_does_not_mutate_source_metadata():
    """Mutating the returned piece's metadata must not leak into the source."""
    metadata = {"json_schema": {"type": "object"}}
    messages = [_message(role="user", content="hi", metadata=metadata)]

    squashed = build_squashed_user_message(new_message_content="hi", source_messages=messages)
    squashed.message_pieces[0].prompt_metadata["new_key"] = "new_value"

    assert "new_key" not in messages[0].message_pieces[0].prompt_metadata
