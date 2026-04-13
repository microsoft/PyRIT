# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests specifically for function call chaining in OpenAIResponseTarget.
These tests verify that conversation history is correctly maintained across
multiple function calls, which is critical for the Responses API.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.memory import CentralMemory
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import OpenAIResponseTarget


@pytest.fixture
def response_target(patch_central_database):
    """Create a test OpenAIResponseTarget."""
    return OpenAIResponseTarget(
        model_name="gpt-4",
        endpoint="https://mock.azure.com",
        api_key="mock-key",
    )


def create_mock_function_call_response(call_id: str, function_name: str, arguments: dict) -> MagicMock:
    """Helper to create a mock SDK response with a function_call."""
    mock_response = MagicMock()
    mock_response.status = "completed"
    mock_response.error = None

    mock_func_section = MagicMock()
    mock_func_section.type = "function_call"
    mock_func_section.call_id = call_id
    mock_func_section.name = function_name
    mock_func_section.arguments = json.dumps(arguments)
    mock_func_section.model_dump.return_value = {
        "type": "function_call",
        "call_id": call_id,
        "name": function_name,
        "arguments": json.dumps(arguments),
    }
    mock_response.output = [mock_func_section]
    return mock_response


def create_mock_text_response(text: str) -> MagicMock:
    """Helper to create a mock SDK response with a text message."""
    mock_response = MagicMock()
    mock_response.status = "completed"
    mock_response.error = None

    mock_msg_section = MagicMock()
    mock_msg_section.type = "message"
    mock_msg_section.content = [MagicMock(text=text)]
    mock_response.output = [mock_msg_section]
    return mock_response


@pytest.mark.asyncio
async def test_single_function_call_preserves_history(response_target, patch_central_database):
    """Test that a single function call maintains proper conversation history."""
    conversation_id = str(uuid.uuid4())

    # Register function
    async def get_weather(args: dict) -> dict:
        return {"temperature": 72, "condition": "sunny"}

    response_target._custom_functions["get_weather"] = get_weather

    # User message
    user_message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="What's the weather?",
                conversation_id=conversation_id,
            )
        ]
    )

    # Mock responses: function_call, then final message
    responses = [
        create_mock_function_call_response("call_1", "get_weather", {"location": "NYC"}),
        create_mock_text_response("It's 72 and sunny!"),
    ]

    call_history = []

    async def mock_create(**kwargs):
        # Capture what's being sent to the API
        call_history.append(kwargs["input"])
        return responses[len(call_history) - 1]

    with patch.object(response_target._async_client.responses, "create", new_callable=AsyncMock) as mock_create_call:
        mock_create_call.side_effect = mock_create

        result = await response_target.send_prompt_async(message=user_message)

        # Verify we got the final message (plus intermediate ones)
        assert len(result) >= 1
        # The last message should be the final text response
        assert result[-1].message_pieces[0].original_value == "It's 72 and sunny!"

        # Verify call history structure
        assert len(call_history) == 2

        # First call: user message only
        first_call_input = call_history[0]
        assert len(first_call_input) == 1
        assert first_call_input[0]["role"] == "user"

        # Second call: user message + function_call + function_call_output
        second_call_input = call_history[1]
        assert len(second_call_input) == 3  # user, function_call, function_call_output

        # Verify the function_call is preserved
        function_call_item = second_call_input[1]
        assert function_call_item["type"] == "function_call"
        assert function_call_item["call_id"] == "call_1"
        assert function_call_item["name"] == "get_weather"

        # Verify the function_call_output references the correct call_id
        function_output_item = second_call_input[2]
        assert function_output_item["type"] == "function_call_output"
        assert function_output_item["call_id"] == "call_1"
        assert "temperature" in function_output_item["output"]


@pytest.mark.asyncio
async def test_chained_function_calls_preserve_all_history(response_target, patch_central_database):
    """Test that multiple chained function calls maintain complete conversation history."""
    conversation_id = str(uuid.uuid4())

    # Register two functions
    async def get_location(args: dict) -> dict:
        return {"location": "New York"}

    async def get_weather(args: dict) -> dict:
        return {"temperature": 68, "condition": "cloudy"}

    response_target._custom_functions["get_location"] = get_location
    response_target._custom_functions["get_weather"] = get_weather

    # User message
    user_message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="What's my local weather?",
                conversation_id=conversation_id,
            )
        ]
    )

    # Mock responses: function_call 1 -> function_call 2 -> final message
    responses = [
        create_mock_function_call_response("call_1", "get_location", {}),
        create_mock_function_call_response("call_2", "get_weather", {"location": "New York"}),
        create_mock_text_response("Your local weather in New York is 68 and cloudy."),
    ]

    call_history = []

    async def mock_create(**kwargs):
        call_history.append(kwargs["input"])
        return responses[len(call_history) - 1]

    with patch.object(response_target._async_client.responses, "create", new_callable=AsyncMock) as mock_create_call:
        mock_create_call.side_effect = mock_create

        result = await response_target.send_prompt_async(message=user_message)

        # Verify the final message contains weather results
        assert len(result) >= 1
        # The last message should contain the result
        assert "68 and cloudy" in result[-1].message_pieces[0].original_value

        # Verify we made 3 API calls
        assert len(call_history) == 3

        # First call: user message only
        assert len(call_history[0]) == 1
        assert call_history[0][0]["role"] == "user"

        # Second call: user + function_call_1 + output_1
        assert len(call_history[1]) == 3
        assert call_history[1][1]["type"] == "function_call"
        assert call_history[1][1]["call_id"] == "call_1"
        assert call_history[1][2]["type"] == "function_call_output"
        assert call_history[1][2]["call_id"] == "call_1"

        # Third call: user + function_call_1 + output_1 + function_call_2 + output_2
        assert len(call_history[2]) == 5
        # Verify first function call and output
        assert call_history[2][1]["type"] == "function_call"
        assert call_history[2][1]["call_id"] == "call_1"
        assert call_history[2][2]["type"] == "function_call_output"
        assert call_history[2][2]["call_id"] == "call_1"
        # Verify second function call and output
        assert call_history[2][3]["type"] == "function_call"
        assert call_history[2][3]["call_id"] == "call_2"
        assert call_history[2][4]["type"] == "function_call_output"
        assert call_history[2][4]["call_id"] == "call_2"


@pytest.mark.asyncio
async def test_function_call_memory_persistence(response_target, patch_central_database):
    """Test that all intermediate function calls are stored in memory."""
    conversation_id = str(uuid.uuid4())

    async def calculate(args: dict) -> dict:
        return {"result": args["x"] * 2}

    response_target._custom_functions["calculate"] = calculate

    user_message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="Double the number 5",
                conversation_id=conversation_id,
            )
        ]
    )

    responses = [
        create_mock_function_call_response("call_x", "calculate", {"x": 5}),
        create_mock_text_response("The result is 10"),
    ]

    call_count = {"n": 0}

    async def mock_create(**kwargs):
        call_count["n"] += 1
        return responses[call_count["n"] - 1]

    with patch.object(response_target._async_client.responses, "create", new_callable=AsyncMock) as mock_create_call:
        mock_create_call.side_effect = mock_create

        await response_target.send_prompt_async(message=user_message)

        # Verify memory does NOT contain intermediate messages
        # (targets no longer persist intermediate messages - normalizer handles that)
        memory = CentralMemory.get_memory_instance()
        conversation = memory.get_conversation(conversation_id=conversation_id)

        # Target doesn't persist messages anymore - all returned messages will be persisted by normalizer
        assert len(conversation) == 0


@pytest.mark.asyncio
async def test_call_id_consistency_across_chain(response_target, patch_central_database):
    """Test that call_ids are correctly maintained throughout the function call chain."""
    conversation_id = str(uuid.uuid4())

    async def func_a(args: dict) -> dict:
        return {"value": "a"}

    async def func_b(args: dict) -> dict:
        return {"value": "b"}

    response_target._custom_functions["func_a"] = func_a
    response_target._custom_functions["func_b"] = func_b

    user_message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="Run functions",
                conversation_id=conversation_id,
            )
        ]
    )

    # Create responses with unique call_ids
    responses = [
        create_mock_function_call_response("call_alpha", "func_a", {}),
        create_mock_function_call_response("call_beta", "func_b", {}),
        create_mock_text_response("Done"),
    ]

    call_history = []

    async def mock_create(**kwargs):
        call_history.append(kwargs["input"])
        return responses[len(call_history) - 1]

    with patch.object(response_target._async_client.responses, "create", new_callable=AsyncMock) as mock_create_call:
        mock_create_call.side_effect = mock_create

        await response_target.send_prompt_async(message=user_message)

        # Third API call should have both function calls with their respective outputs
        third_call = call_history[2]

        # Extract all function_call_output items
        outputs = [item for item in third_call if item.get("type") == "function_call_output"]
        assert len(outputs) == 2

        # Verify call_ids match
        call_ids = {output["call_id"] for output in outputs}
        assert "call_alpha" in call_ids
        assert "call_beta" in call_ids

        # Verify each output follows its corresponding function_call
        for i, item in enumerate(third_call):
            if item.get("type") == "function_call" and i + 1 < len(third_call):
                next_item = third_call[i + 1]
                if next_item.get("type") == "function_call_output":
                    assert item["call_id"] == next_item["call_id"]


@pytest.mark.asyncio
async def test_no_duplicate_messages_in_conversation(response_target, patch_central_database):
    """Test that messages aren't duplicated when building conversation history."""
    conversation_id = str(uuid.uuid4())

    async def noop(args: dict) -> dict:
        return {"status": "ok"}

    response_target._custom_functions["noop"] = noop

    user_message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="Test",
                conversation_id=conversation_id,
            )
        ]
    )

    responses = [
        create_mock_function_call_response("call_1", "noop", {}),
        create_mock_text_response("Complete"),
    ]

    call_history = []

    async def mock_create(**kwargs):
        input_items = kwargs["input"]
        call_history.append(input_items)

        # Check for duplicates in the input
        seen_items = []
        for item in input_items:
            item_str = json.dumps(item, sort_keys=True)
            assert item_str not in seen_items, f"Duplicate item detected in API call: {item}"
            seen_items.append(item_str)

        return responses[len(call_history) - 1]

    with patch.object(response_target._async_client.responses, "create", new_callable=AsyncMock) as mock_create_call:
        mock_create_call.side_effect = mock_create

        await response_target.send_prompt_async(message=user_message)

        # If we got here, no duplicates were detected
        assert len(call_history) == 2
