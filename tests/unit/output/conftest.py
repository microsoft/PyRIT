# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest

from pyrit.models import Message, MessagePiece


@pytest.fixture
def reasoning_value() -> str:
    return json.dumps(
        {
            "id": "reasoning_123",
            "type": "reasoning",
            "summary": [
                {"type": "summary_text", "text": "step one"},
                {"type": "summary_text", "text": "step two"},
            ],
            "status": "completed",
        }
    )


@pytest.fixture
def empty_reasoning_value() -> str:
    return json.dumps(
        {
            "id": "reasoning_empty",
            "type": "reasoning",
            "summary": [],
            "status": "completed",
        }
    )


@pytest.fixture
def reasoning_message(reasoning_value: str) -> Message:
    return Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=reasoning_value,
                converted_value=reasoning_value,
                original_value_data_type="reasoning",
                converted_value_data_type="reasoning",
            ),
            MessagePiece(
                role="assistant",
                original_value="Final answer.",
                converted_value="Final answer.",
            ),
        ]
    )
