# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import uuid
from pathlib import Path

import pytest

from pyrit.auth import (
    get_azure_openai_auth,
    get_azure_token_provider,
)
from pyrit.common.path import HOME_PATH
from pyrit.executor.attack import PromptSendingAttack
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import (
    OpenAIChatTarget,
    OpenAIImageTarget,
    OpenAIResponseTarget,
    OpenAITTSTarget,
    OpenAIVideoTarget,
    PromptShieldTarget,
    RealtimeTarget,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "model_name", "supports_seed"),
    [
        ("AZURE_OPENAI_GPT4O_ENDPOINT", "AZURE_OPENAI_GPT4O_MODEL", True),
        ("AZURE_OPENAI_GPT4O_ENDPOINT2", "AZURE_OPENAI_GPT4O_MODEL2", True),
        ("AZURE_OPENAI_GPT4O_AAD_ENDPOINT", "AZURE_OPENAI_GPT4O_AAD_MODEL", True),
        ("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT", "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL", True),
        ("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT2", "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL2", True),
        ("AZURE_OPENAI_INTEGRATION_TEST_ENDPOINT", "AZURE_OPENAI_INTEGRATION_TEST_MODEL", True),
        ("AZURE_OPENAI_GPT4O_STRICT_FILTER_ENDPOINT", "AZURE_OPENAI_GPT4O_STRICT_FILTER_MODEL", True),
        ("AZURE_OPENAI_GPT3_5_CHAT_ENDPOINT", "AZURE_OPENAI_GPT3_5_CHAT_MODEL", True),
        ("AZURE_OPENAI_GPT4_CHAT_ENDPOINT", "AZURE_OPENAI_GPT4_CHAT_MODEL", True),
        ("AZURE_OPENAI_GPT5_COMPLETIONS_ENDPOINT", "AZURE_OPENAI_GPT5_COMPLETIONS_MODEL", True),
        ("AZURE_OPENAI_GPTV_CHAT_ENDPOINT", "AZURE_OPENAI_GPTV_CHAT_MODEL", True),
        ("AZURE_FOUNDRY_MISTRAL_LARGE_ENDPOINT", "AZURE_FOUNDRY_MISTRAL_LARGE_MODEL", True),
    ],
)
async def test_openai_chat_target_entra_auth(sqlite_instance, endpoint, model_name, supports_seed):
    endpoint_value = os.environ[endpoint]
    args = {
        "endpoint": endpoint_value,
        "temperature": 0.0,
        "api_key": get_azure_openai_auth(endpoint_value),
        "model_name": os.environ[model_name] if model_name else None,
    }

    if supports_seed:
        args["seed"] = 42

    # These endpoints should have Entra authentication enabled in the current context
    # e.g. Cognitive Services OpenAI Contributor or Cognitive Services User/Contributor role (for non-OpenAI resources)
    target = OpenAIChatTarget(**args)

    attack = PromptSendingAttack(objective_target=target)
    result = await attack.execute_async(objective="Hello, how are you?")
    assert result is not None
    assert result.last_response is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "model_name"),
    [
        ("OPENAI_IMAGE_ENDPOINT", "OPENAI_IMAGE_MODEL"),
        ("OPENAI_IMAGE_ENDPOINT2", "OPENAI_IMAGE_MODEL2"),
    ],
)
async def test_openai_image_target_entra_auth(sqlite_instance, endpoint, model_name):
    endpoint_value = os.environ[endpoint]
    args = {
        "endpoint": endpoint_value,
        "api_key": get_azure_openai_auth(endpoint_value),
        "model_name": os.environ[model_name],
    }

    target = OpenAIImageTarget(**args)

    attack = PromptSendingAttack(objective_target=target)
    result = await attack.execute_async(objective="A cute baby sea otter")
    assert result is not None
    assert result.last_response is not None


# Path to sample image file for image editing tests
SAMPLE_IMAGE_FILE = HOME_PATH / "assets" / "pyrit_architecture.png"


@pytest.mark.asyncio
async def test_openai_image_editing_single_image_entra_auth(sqlite_instance):
    """
    Test image editing with a single image input using Entra authentication.
    Uses gpt-image-1 (OPENAI_IMAGE_ENDPOINT2) which supports image editing/remix.

    Verifies that:
    1. Entra authentication works correctly for image editing
    2. A text prompt + single image generates a modified image
    """
    endpoint = os.environ["OPENAI_IMAGE_ENDPOINT2"]
    target = OpenAIImageTarget(
        endpoint=endpoint,
        model_name=os.environ["OPENAI_IMAGE_MODEL2"],
        api_key=get_azure_openai_auth(endpoint),
    )

    conv_id = str(uuid.uuid4())
    text_piece = MessagePiece(
        role="user",
        original_value="Make this image grayscale",
        original_value_data_type="text",
        conversation_id=conv_id,
    )
    image_piece = MessagePiece(
        role="user",
        original_value=str(SAMPLE_IMAGE_FILE),
        original_value_data_type="image_path",
        conversation_id=conv_id,
    )

    message = Message(message_pieces=[text_piece, image_piece])
    result = await target.send_prompt_async(message=message)

    assert result is not None
    assert len(result) >= 1
    assert result[0].message_pieces[0].response_error == "none"

    # Validate we got a valid image file path
    output_path = Path(result[0].message_pieces[0].converted_value)
    assert output_path.exists(), f"Output image file not found at path: {output_path}"
    assert output_path.is_file(), f"Path exists but is not a file: {output_path}"


@pytest.mark.asyncio
async def test_openai_image_editing_multiple_images_entra_auth(sqlite_instance):
    """
    Test image editing with multiple image inputs using Entra authentication.
    Uses gpt-image-1 which supports 1-16 image inputs.

    Verifies that:
    1. Entra authentication works for multi-image editing
    2. Multiple images can be passed to the edit endpoint
    """
    endpoint = os.environ["OPENAI_IMAGE_ENDPOINT2"]
    target = OpenAIImageTarget(
        endpoint=endpoint,
        model_name=os.environ["OPENAI_IMAGE_MODEL2"],
        api_key=get_azure_openai_auth(endpoint),
    )

    conv_id = str(uuid.uuid4())
    text_piece = MessagePiece(
        role="user",
        original_value="Blend these images together",
        original_value_data_type="text",
        conversation_id=conv_id,
    )
    image_piece1 = MessagePiece(
        role="user",
        original_value=str(SAMPLE_IMAGE_FILE),
        original_value_data_type="image_path",
        conversation_id=conv_id,
    )
    image_piece2 = MessagePiece(
        role="user",
        original_value=str(SAMPLE_IMAGE_FILE),
        original_value_data_type="image_path",
        conversation_id=conv_id,
    )

    message = Message(message_pieces=[text_piece, image_piece1, image_piece2])
    result = await target.send_prompt_async(message=message)

    assert result is not None
    assert len(result) >= 1
    assert result[0].message_pieces[0].response_error == "none"

    # Validate we got a valid image file path
    output_path = Path(result[0].message_pieces[0].converted_value)
    assert output_path.exists(), f"Output image file not found at path: {output_path}"
    assert output_path.is_file(), f"Path exists but is not a file: {output_path}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "model_name"),
    [
        ("OPENAI_TTS_ENDPOINT1", "OPENAI_TTS_MODEL1"),
        ("OPENAI_TTS_ENDPOINT2", "OPENAI_TTS_MODEL2"),
    ],
)
async def test_openai_tts_target_entra_auth(sqlite_instance, endpoint, model_name):
    endpoint_value = os.environ[endpoint]
    args = {
        "endpoint": endpoint_value,
        "api_key": get_azure_openai_auth(endpoint_value),
        "model_name": os.environ[model_name],
    }

    target = OpenAITTSTarget(**args)

    attack = PromptSendingAttack(objective_target=target)
    result = await attack.execute_async(objective="Hello, how are you?")
    assert result is not None
    assert result.last_response is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "model_name"),
    [
        ("OPENAI_RESPONSES_ENDPOINT", "OPENAI_RESPONSES_MODEL"),
        ("AZURE_OPENAI_GPT41_RESPONSES_ENDPOINT", "AZURE_OPENAI_GPT41_RESPONSES_MODEL"),
        ("AZURE_OPENAI_GPT5_RESPONSES_ENDPOINT", "AZURE_OPENAI_GPT5_MODEL"),
    ],
)
async def test_openai_responses_target_entra_auth(sqlite_instance, endpoint, model_name):
    endpoint_value = os.environ[endpoint]
    args = {
        "endpoint": endpoint_value,
        "model_name": os.environ[model_name],
        "api_key": get_azure_openai_auth(endpoint_value),
    }

    target = OpenAIResponseTarget(**args)

    attack = PromptSendingAttack(objective_target=target)
    result = await attack.execute_async(objective="Hello, how are you?")
    assert result is not None
    assert result.last_response is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "model_name"),
    [
        ("OPENAI_RESPONSES_ENDPOINT", "OPENAI_RESPONSES_MODEL"),
        ("AZURE_OPENAI_GPT5_RESPONSES_ENDPOINT", "AZURE_OPENAI_GPT5_MODEL"),
    ],
)
async def test_openai_responses_target_reasoning_effort_entra_auth(sqlite_instance, endpoint, model_name):
    endpoint_value = os.environ[endpoint]
    args = {
        "endpoint": endpoint_value,
        "model_name": os.environ[model_name],
        "api_key": get_azure_openai_auth(endpoint_value),
        "reasoning_effort": "low",
    }

    target = OpenAIResponseTarget(**args)

    attack = PromptSendingAttack(objective_target=target)
    result = await attack.execute_async(objective="What is 2 + 2?")
    assert result is not None
    assert result.last_response is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "model_name"),
    [
        ("OPENAI_RESPONSES_ENDPOINT", "OPENAI_RESPONSES_MODEL"),
        ("AZURE_OPENAI_GPT5_RESPONSES_ENDPOINT", "AZURE_OPENAI_GPT5_MODEL"),
    ],
)
async def test_openai_responses_target_reasoning_summary_entra_auth(sqlite_instance, endpoint, model_name):
    endpoint_value = os.environ[endpoint]
    args = {
        "endpoint": endpoint_value,
        "model_name": os.environ[model_name],
        "api_key": get_azure_openai_auth(endpoint_value),
        "reasoning_effort": "low",
        "reasoning_summary": "auto",
    }

    target = OpenAIResponseTarget(**args)

    attack = PromptSendingAttack(objective_target=target)
    result = await attack.execute_async(objective="What is 2 + 2?")
    assert result is not None
    assert result.last_response is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "model_name"),
    [
        ("OPENAI_REALTIME_ENDPOINT", "OPENAI_REALTIME_MODEL"),
    ],
)
async def test_openai_realtime_target_entra_auth(sqlite_instance, endpoint, model_name):
    endpoint_value = os.environ[endpoint]
    model_name_value = os.environ[model_name]
    target = RealtimeTarget(
        endpoint=endpoint_value,
        model_name=model_name_value,
        api_key=get_azure_openai_auth(endpoint_value),
    )

    attack = PromptSendingAttack(objective_target=target)
    result = await attack.execute_async(objective="Hello, how are you?")
    assert result is not None
    assert result.last_response is not None


@pytest.mark.asyncio
async def test_video_target_entra_auth(sqlite_instance):
    # Takes a long time and sometimes encounters retry errors.
    # Note: AZURE_OPENAI_VIDEO_ENDPOINT should be configured for Sora v2 API
    endpoint = os.environ["AZURE_OPENAI_VIDEO_ENDPOINT"]
    target = OpenAIVideoTarget(
        endpoint=endpoint,
        model_name=os.environ["AZURE_OPENAI_VIDEO_MODEL"],
        api_key=get_azure_openai_auth(endpoint),
    )
    attack = PromptSendingAttack(objective_target=target)
    result = await attack.execute_async(objective="test")
    assert result is not None
    assert result.last_response is not None


@pytest.mark.asyncio
async def test_video_target_remix_entra_auth(sqlite_instance):
    """Test video remix mode with Entra authentication."""
    endpoint = os.environ["OPENAI_VIDEO_ENDPOINT"]
    target = OpenAIVideoTarget(
        endpoint=endpoint,
        model_name=os.environ["OPENAI_VIDEO_MODEL"],
        api_key=get_azure_openai_auth(endpoint),
        n_seconds=4,
    )

    # Generate initial video
    text_piece = MessagePiece(
        role="user",
        original_value="A bird flying over a lake",
        converted_value="A bird flying over a lake",
    )
    result = await target.send_prompt_async(message=Message([text_piece]))
    response_piece = result[0].message_pieces[0]
    assert response_piece.response_error == "none"
    video_id = response_piece.prompt_metadata.get("video_id")
    assert video_id

    # Remix
    remix_piece = MessagePiece(
        role="user",
        original_value="Add a sunset",
        converted_value="Add a sunset",
        prompt_metadata={"video_id": video_id},
    )
    remix_result = await target.send_prompt_async(message=Message([remix_piece]))
    assert remix_result[0].message_pieces[0].response_error == "none"


@pytest.mark.asyncio
async def test_prompt_shield_target_entra_auth(sqlite_instance):
    # Make sure to assign the Cognitive Services User or Contributor role
    endpoint = os.environ["AZURE_CONTENT_SAFETY_API_ENDPOINT"]
    target = PromptShieldTarget(
        endpoint=endpoint,
        api_key=get_azure_token_provider("https://cognitiveservices.azure.com/.default"),
        field="userPrompt",
    )
    attack = PromptSendingAttack(objective_target=target)
    result = await attack.execute_async(objective="test")
    assert result is not None
    assert result.last_response is not None


@pytest.mark.asyncio
async def test_openai_chat_target_with_sync_token_provider(sqlite_instance):
    """Test that OpenAIChatTarget works with synchronous token providers (auto-wrapped)."""
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    endpoint = os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"]
    model_name = os.environ["AZURE_OPENAI_GPT4O_MODEL"]

    # Use synchronous token provider - this should be auto-wrapped
    sync_token_provider = get_bearer_token_provider(
        DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
    )

    target = OpenAIChatTarget(
        endpoint=endpoint,
        model_name=model_name,
        api_key=sync_token_provider,
        temperature=0.0,
        seed=42,
    )

    attack = PromptSendingAttack(objective_target=target)
    result = await attack.execute_async(objective="Hello, how are you?")
    assert result is not None
    assert result.last_response is not None
