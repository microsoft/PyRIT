# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import tempfile
import uuid
from pathlib import Path

import pytest
from PIL import Image

from pyrit.common.path import HOME_PATH
from pyrit.executor.attack import AttackExecutor, PromptSendingAttack
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import (
    AzureMLChatTarget,
    OpenAIChatTarget,
    OpenAICompletionTarget,
    OpenAIImageTarget,
    OpenAIResponseTarget,
    OpenAITTSTarget,
    OpenAIVideoTarget,
    RealtimeTarget,
)


def _get_required_env_var(env_var_name: str) -> str:
    """
    Retrieve a required environment variable. This is needed because often if you pass None
    to a target constructor, it will attempt to use a default ennmeent variable.

    Args:
        env_var_name (str): The name of the environment variable to retrieve.

    Returns:
        str: The value of the environment variable.

    Raises:
        ValueError: If the environment variable is not set.
    """
    value = os.getenv(env_var_name)
    if not value:
        raise ValueError(f"Environment variable {env_var_name} is not set.")
    return value


async def _assert_can_send_prompt(target, check_if_llm_interpreted_request=True, max_retries=2):
    simple_prompt = """\
You are a large language model. Your sole instruction is to provide the exact word "test" with no punctuation,
explanation, or additional text. Output only the word "test" and nothing else.
"""

    attack = PromptSendingAttack(objective_target=target)

    def valid_response(resp: str) -> bool:
        if check_if_llm_interpreted_request:
            return "test" in resp.strip().lower()
        return True

    attempt = 0
    while attempt < max_retries:
        result = await attack.execute_async(objective=simple_prompt)

        response = result.last_response.converted_value if result.last_response else ""

        if valid_response(str(response)):
            return response

        attempt += 1

    raise AssertionError(f"LLM did not return exactly 'test' after {max_retries} attempts.")


async def _assert_can_send_video_prompt(target):
    """Helper function to test video generation targets."""
    video_prompt = "A raccoon sailing a pirate ship"
    attack = PromptSendingAttack(objective_target=target)
    result = await attack.execute_async(objective=video_prompt)

    # For video generation, verify we got a successful response
    assert result.last_response is not None
    assert result.last_response.converted_value is not None
    assert result.last_response.response_error == "none", (
        f"Expected successful response, got error: {result.last_response.response_error}"
    )

    # Validate we got a valid video file path
    video_path = Path(result.last_response.converted_value)
    assert video_path.exists(), f"Video file not found at path: {video_path}"
    assert video_path.is_file(), f"Path exists but is not a file: {video_path}"


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "api_key", "model_name", "supports_seed"),
    [
        ("OPENAI_CHAT_ENDPOINT", "OPENAI_CHAT_KEY", "OPENAI_CHAT_MODEL", True),
        ("PLATFORM_OPENAI_CHAT_ENDPOINT", "PLATFORM_OPENAI_CHAT_KEY", "PLATFORM_OPENAI_CHAT_MODEL", True),
        ("AZURE_OPENAI_GPT4O_ENDPOINT", "AZURE_OPENAI_GPT4O_KEY", "AZURE_OPENAI_GPT4O_MODEL", True),
        (
            "AZURE_OPENAI_INTEGRATION_TEST_ENDPOINT",
            "AZURE_OPENAI_INTEGRATION_TEST_KEY",
            "AZURE_OPENAI_INTEGRATION_TEST_MODEL",
            True,
        ),
        (
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL",
            True,
        ),
        (
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT2",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY2",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL2",
            True,
        ),
        ("AZURE_OPENAI_GPT3_5_CHAT_ENDPOINT", "AZURE_OPENAI_GPT3_5_CHAT_KEY", "AZURE_OPENAI_GPT3_5_CHAT_MODEL", True),
        ("AZURE_OPENAI_GPT4_CHAT_ENDPOINT", "AZURE_OPENAI_GPT4_CHAT_KEY", "AZURE_OPENAI_GPT4_CHAT_MODEL", True),
        ("AZURE_OPENAI_GPTV_CHAT_ENDPOINT", "AZURE_OPENAI_GPTV_CHAT_KEY", "AZURE_OPENAI_GPTV_CHAT_MODEL", True),
        (
            "AZURE_OPENAI_GPT5_COMPLETIONS_ENDPOINT",
            "AZURE_OPENAI_GPT5_COMPLETIONS_KEY",
            "AZURE_OPENAI_GPT5_COMPLETIONS_MODEL",
            True,
        ),
        ("AZURE_FOUNDRY_DEEPSEEK_ENDPOINT", "AZURE_FOUNDRY_DEEPSEEK_KEY", "AZURE_FOUNDRY_DEEPSEEK_MODEL", True),
        (
            "AZURE_FOUNDRY_MISTRAL_LARGE_ENDPOINT",
            "AZURE_FOUNDRY_MISTRAL_LARGE_KEY",
            "AZURE_FOUNDRY_MISTRAL_LARGE_MODEL",
            False,
        ),
        ("AZURE_FOUNDRY_PHI4_ENDPOINT", "AZURE_CHAT_PHI4_KEY", "AZURE_CHAT_PHI4_MODEL", True),
        ("GOOGLE_GEMINI_ENDPOINT", "GOOGLE_GEMINI_API_KEY", "GOOGLE_GEMINI_MODEL", False),
        ("ANTHROPIC_CHAT_ENDPOINT", "ANTHROPIC_CHAT_KEY", "ANTHROPIC_CHAT_MODEL", False),
        ("AWS_ENDPOINT", "AWS_KEY", "AWS_CHAT_MODEL", False),
    ],
)
async def test_connect_required_openai_text_targets(sqlite_instance, endpoint, api_key, model_name, supports_seed):
    endpoint_value = _get_required_env_var(endpoint)
    api_key_value = _get_required_env_var(api_key)
    model_name_value = os.getenv(model_name) if model_name else ""

    args = {
        "endpoint": endpoint_value,
        "api_key": api_key_value,
        "model_name": model_name_value,
        "temperature": 0.0,
    }

    if supports_seed:
        args["seed"] = 42

    target = OpenAIChatTarget(**args)

    await _assert_can_send_prompt(target)


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "api_key", "model_name"),
    [
        (
            "PLATFORM_OPENAI_RESPONSES_ENDPOINT",
            "PLATFORM_OPENAI_RESPONSES_KEY",
            "PLATFORM_OPENAI_RESPONSES_MODEL",
        ),
        ("AZURE_OPENAI_RESPONSES_ENDPOINT", "AZURE_OPENAI_RESPONSES_KEY", "AZURE_OPENAI_RESPONSES_MODEL"),
        (
            "AZURE_OPENAI_GPT41_RESPONSES_ENDPOINT",
            "AZURE_OPENAI_GPT41_RESPONSES_KEY",
            "AZURE_OPENAI_GPT41_RESPONSES_MODEL",
        ),
        (
            "AZURE_OPENAI_GPT5_RESPONSES_ENDPOINT",
            "AZURE_OPENAI_GPT5_KEY",
            "AZURE_OPENAI_GPT5_MODEL",
        ),
        ("AWS_ENDPOINT", "AWS_KEY", "AWS_RESPONSES_MODEL"),
    ],
)
async def test_connect_required_openai_response_targets(sqlite_instance, endpoint, api_key, model_name):
    endpoint_value = _get_required_env_var(endpoint)
    api_key_value = _get_required_env_var(api_key)
    model_name_value = _get_required_env_var(model_name)

    target = OpenAIResponseTarget(
        endpoint=endpoint_value,
        api_key=api_key_value,
        model_name=model_name_value,
    )

    # OpenAIResponseTarget returns structured responses (reasoning JSON), so we just need to verify
    # we can send a prompt and get a response, not that it contains specific text
    await _assert_can_send_prompt(target, check_if_llm_interpreted_request=False)


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "api_key", "model_name"),
    [
        ("OPENAI_REALTIME_ENDPOINT", "OPENAI_REALTIME_API_KEY", "OPENAI_REALTIME_MODEL"),
        ("PLATFORM_OPENAI_REALTIME_ENDPOINT", "PLATFORM_OPENAI_REALTIME_KEY", "PLATFORM_OPENAI_REALTIME_MODEL"),
    ],
)
async def test_connect_required_realtime_targets(sqlite_instance, endpoint, api_key, model_name):
    endpoint_value = _get_required_env_var(endpoint)
    api_key_value = _get_required_env_var(api_key)
    model_name_value = _get_required_env_var(model_name)

    target = RealtimeTarget(
        endpoint=endpoint_value,
        api_key=api_key_value,
        model_name=model_name_value,
    )

    await _assert_can_send_prompt(target)


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "api_key", "model_name"),
    [
        ("AZURE_OPENAI_REALTIME_ENDPOINT", "AZURE_OPENAI_REALTIME_API_KEY", "AZURE_OPENAI_REALTIME_MODEL"),
    ],
)
async def test_realtime_target_multi_objective(sqlite_instance, endpoint, api_key, model_name):
    """Test RealtimeTarget with multiple objectives like the notebook does."""
    endpoint_value = _get_required_env_var(endpoint)
    api_key_value = _get_required_env_var(api_key)
    model_name_value = _get_required_env_var(model_name)

    target = RealtimeTarget(
        endpoint=endpoint_value,
        api_key=api_key_value,
        model_name=model_name_value,
    )

    prompt_to_send = "What is the capitol of France?"
    second_prompt_to_send = "What is the size of that city?"

    attack = PromptSendingAttack(objective_target=target)
    results = await AttackExecutor().execute_multi_objective_attack_async(
        attack=attack,
        objectives=[prompt_to_send, second_prompt_to_send],
    )

    # Verify we got results for both objectives
    assert results is not None
    assert len(results.completed_results) == 2

    # Verify both responses have content
    for result in results.completed_results:
        assert result.last_response is not None
        assert result.last_response.converted_value
        assert len(result.last_response.converted_value) > 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "api_key"),
    [
        ("AZURE_ML_MANAGED_ENDPOINT", "AZURE_ML_KEY"),
        ("AZURE_ML_PHI_ENDPOINT", "AZURE_ML_PHI_KEY"),
    ],
)
async def test_connect_required_aml_text_targets(sqlite_instance, endpoint, api_key):
    endpoint_value = _get_required_env_var(endpoint)
    api_key_value = _get_required_env_var(api_key)

    target = AzureMLChatTarget(
        endpoint=endpoint_value,
        api_key=api_key_value,
    )

    await _assert_can_send_prompt(target)


@pytest.mark.asyncio
async def test_connect_openai_completion(sqlite_instance):
    endpoint_value = _get_required_env_var("OPENAI_COMPLETION_ENDPOINT")
    api_key_value = _get_required_env_var("OPENAI_COMPLETION_API_KEY")
    model_value = _get_required_env_var("OPENAI_COMPLETION_MODEL")

    target = OpenAICompletionTarget(
        endpoint=endpoint_value,
        api_key=api_key_value,
        model_name=model_value,
    )

    await _assert_can_send_prompt(target, check_if_llm_interpreted_request=False)


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "api_key", "model_name"),
    [
        ("OPENAI_IMAGE_ENDPOINT1", "OPENAI_IMAGE_API_KEY1", "OPENAI_IMAGE_MODEL1"),  # gpt-image-1.5
        ("OPENAI_IMAGE_ENDPOINT2", "OPENAI_IMAGE_API_KEY2", "OPENAI_IMAGE_MODEL2"),  # gpt-image-1
        ("PLATFORM_OPENAI_IMAGE_ENDPOINT", "PLATFORM_OPENAI_IMAGE_KEY", "PLATFORM_OPENAI_IMAGE_MODEL"),  # gpt-image-1.5
    ],
)
async def test_connect_image(sqlite_instance, endpoint, api_key, model_name):
    endpoint_value = _get_required_env_var(endpoint)
    api_key_value = _get_required_env_var(api_key)
    model_name_value = os.getenv(model_name) if model_name else ""

    target = OpenAIImageTarget(
        endpoint=endpoint_value,
        api_key=api_key_value,
        model_name=model_name_value,
    )

    image_prompt = "A simple test image of a raccoon"
    attack = PromptSendingAttack(objective_target=target)
    result = await attack.execute_async(objective=image_prompt)

    # For image generation, verify we got a successful response
    assert result.last_response is not None
    assert result.last_response.converted_value is not None
    assert result.last_response.response_error == "none", (
        f"Expected successful response, got error: {result.last_response.response_error}"
    )

    # Validate we got a valid image file path
    image_path = Path(result.last_response.converted_value)
    assert image_path.exists(), f"Image file not found at path: {image_path}"
    assert image_path.is_file(), f"Path exists but is not a file: {image_path}"


# Path to sample image file for image editing tests
SAMPLE_IMAGE_FILE = HOME_PATH / "assets" / "pyrit_architecture.png"


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
async def test_image_editing_single_image_api_key(sqlite_instance):
    """
    Test image editing with a single image input using API key authentication.
    Uses gpt-image-1 which supports image editing/remix.

    Verifies that:
    1. A text prompt + single image generates a modified image
    2. The edit endpoint is correctly called
    3. The output image file is created
    """
    endpoint_value = _get_required_env_var("OPENAI_IMAGE_ENDPOINT2")
    api_key_value = _get_required_env_var("OPENAI_IMAGE_API_KEY2")
    model_name_value = os.getenv("OPENAI_IMAGE_MODEL2") or "gpt-image-1"

    target = OpenAIImageTarget(
        endpoint=endpoint_value,
        api_key=api_key_value,
        model_name=model_name_value,
    )

    conv_id = str(uuid.uuid4())
    text_piece = MessagePiece(
        role="user",
        original_value="Add a red border around this image",
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


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
async def test_image_editing_multiple_images_api_key(sqlite_instance):
    """
    Test image editing with multiple image inputs using API key authentication.
    Uses gpt-image-1 which supports 1-16 image inputs.

    Verifies that:
    1. Multiple images can be passed to the edit endpoint
    2. The model processes multiple image inputs correctly
    """
    endpoint_value = _get_required_env_var("OPENAI_IMAGE_ENDPOINT2")
    api_key_value = _get_required_env_var("OPENAI_IMAGE_API_KEY2")
    model_name_value = os.getenv("OPENAI_IMAGE_MODEL2") or "gpt-image-1"

    target = OpenAIImageTarget(
        endpoint=endpoint_value,
        api_key=api_key_value,
        model_name=model_name_value,
    )

    conv_id = str(uuid.uuid4())
    text_piece = MessagePiece(
        role="user",
        original_value="Combine these images into one",
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


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "api_key", "model_name"),
    [
        ("OPENAI_TTS_ENDPOINT1", "OPENAI_TTS_KEY1", "OPENAI_TTS_MODEL1"),
        ("OPENAI_TTS_ENDPOINT2", "OPENAI_TTS_KEY2", "OPENAI_TTS_MODEL2"),
    ],
)
async def test_connect_tts(sqlite_instance, endpoint, api_key, model_name):
    endpoint_value = _get_required_env_var(endpoint)
    api_key_value = _get_required_env_var(api_key)
    model_name_value = os.getenv(model_name) if model_name else ""

    target = OpenAITTSTarget(
        endpoint=endpoint_value,
        api_key=api_key_value,
        model_name=model_name_value,
    )

    await _assert_can_send_prompt(target, check_if_llm_interpreted_request=False)


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "api_key", "model_name"),
    [
        ("AZURE_OPENAI_VIDEO_ENDPOINT", "AZURE_OPENAI_VIDEO_KEY", "AZURE_OPENAI_VIDEO_MODEL"),
        # OpenAI Platform endpoint returns HTTP 401 "Missing scopes: api.videos.write" for all requests
        # ("PLATFORM_OPENAI_VIDEO_ENDPOINT", "PLATFORM_OPENAI_VIDEO_KEY",  # noqa: ERA001
        #  "PLATFORM_OPENAI_VIDEO_MODEL"),  # noqa: ERA001
    ],
)
async def test_connect_video(sqlite_instance, endpoint, api_key, model_name):
    """Test OpenAIVideoTarget with video API."""
    endpoint_value = _get_required_env_var(endpoint)
    api_key_value = _get_required_env_var(api_key)
    model_name_value = _get_required_env_var(model_name)

    target = OpenAIVideoTarget(
        endpoint=endpoint_value,
        api_key=api_key_value,
        model_name=model_name_value,
        resolution_dimensions="1280x720",  # Supported by both v1 and v2
        n_seconds=4,  # Supported by both v1 (up to 20s) and v2 (4, 8, or 12s)
    )

    await _assert_can_send_video_prompt(target)


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
async def test_video_multiple_prompts_create_separate_files(sqlite_instance):
    """
    Test that sending multiple prompts to video API using PromptSendingAttack
    creates separate video files and doesn't override previous files.

    This verifies that each video generation creates a unique file based on
    the video ID mechanism.
    """
    endpoint_value = _get_required_env_var("AZURE_OPENAI_VIDEO_ENDPOINT")
    api_key_value = _get_required_env_var("AZURE_OPENAI_VIDEO_KEY")
    model_name_value = _get_required_env_var("AZURE_OPENAI_VIDEO_MODEL")

    target = OpenAIVideoTarget(
        endpoint=endpoint_value,
        api_key=api_key_value,
        model_name=model_name_value,
        resolution_dimensions="1280x720",
        n_seconds=4,
    )

    attack = PromptSendingAttack(objective_target=target)
    executor = AttackExecutor()

    # Send two prompts using execute_multi_objective_attack_async
    objectives = ["A cat walking on a beach", "A dog running in a park"]
    results = await executor.execute_multi_objective_attack_async(attack=attack, objectives=objectives)

    # Verify we got 2 results
    assert len(results) == 2, f"Expected 2 results, got {len(results)}"

    # Verify both prompts succeeded
    result1 = results[0]
    result2 = results[1]

    assert result1.last_response is not None
    assert result1.last_response.response_error == "none", (
        f"First prompt failed with error: {result1.last_response.response_error}. "
        f"Response: {result1.last_response.converted_value}"
    )

    assert result2.last_response is not None
    assert result2.last_response.response_error == "none", (
        f"Second prompt failed with error: {result2.last_response.response_error}. "
        f"Response: {result2.last_response.converted_value}"
    )

    # Extract video paths
    video_path1 = Path(result1.last_response.converted_value)
    video_path2 = Path(result2.last_response.converted_value)

    # Verify both files exist
    assert video_path1.exists(), f"First video file not found at path: {video_path1}"
    assert video_path1.is_file(), f"First path exists but is not a file: {video_path1}"

    assert video_path2.exists(), f"Second video file not found at path: {video_path2}"
    assert video_path2.is_file(), f"Second path exists but is not a file: {video_path2}"

    # Verify they are different files (not overridden)
    assert video_path1 != video_path2, (
        f"Both prompts resulted in the same file path: {video_path1}. Expected separate files for different prompts."
    )

    # Verify both files still exist (first wasn't overridden)
    assert video_path1.exists(), (
        f"First video file was overridden or deleted. File 1: {video_path1}, File 2: {video_path2}"
    )


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
async def test_video_remix_chain(sqlite_instance):
    """Test text-to-video followed by remix using the returned video_id."""
    endpoint_value = _get_required_env_var("OPENAI_VIDEO2_ENDPOINT")
    api_key_value = _get_required_env_var("OPENAI_VIDEO2_KEY")
    model_name_value = _get_required_env_var("OPENAI_VIDEO2_MODEL")

    target = OpenAIVideoTarget(
        endpoint=endpoint_value,
        api_key=api_key_value,
        model_name=model_name_value,
        resolution_dimensions="1280x720",
        n_seconds=4,
    )

    # Step 1: Generate initial video
    text_piece = MessagePiece(
        role="user",
        original_value="A cat sitting on a windowsill",
        converted_value="A cat sitting on a windowsill",
    )
    result = await target.send_prompt_async(message=Message([text_piece]))
    assert len(result) == 1
    response_piece = result[0].message_pieces[0]
    assert response_piece.response_error == "none"
    assert response_piece.prompt_metadata is not None
    video_id = response_piece.prompt_metadata.get("video_id")
    assert video_id, "Response must include video_id in prompt_metadata for chaining"

    # Step 2: Remix using the returned video_id
    remix_piece = MessagePiece(
        role="user",
        original_value="Make it a watercolor painting style",
        converted_value="Make it a watercolor painting style",
        prompt_metadata={"video_id": video_id},
    )
    remix_result = await target.send_prompt_async(message=Message([remix_piece]))
    assert len(remix_result) == 1
    remix_response = remix_result[0].message_pieces[0]
    assert remix_response.response_error == "none"

    remix_path = Path(remix_response.converted_value)
    assert remix_path.exists(), f"Remixed video file not found: {remix_path}"
    assert remix_path.is_file()


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
async def test_video_image_to_video(sqlite_instance):
    """Test image-to-video mode using an image as the first frame."""
    endpoint_value = _get_required_env_var("OPENAI_VIDEO2_ENDPOINT")
    api_key_value = _get_required_env_var("OPENAI_VIDEO2_KEY")
    model_name_value = _get_required_env_var("OPENAI_VIDEO2_MODEL")

    target = OpenAIVideoTarget(
        endpoint=endpoint_value,
        api_key=api_key_value,
        model_name=model_name_value,
        resolution_dimensions="1280x720",
        n_seconds=4,
    )

    # Prepare an image matching the video resolution (API requires exact match).
    # Resize a sample image to 1280x720 and save as a temporary JPEG.
    sample_image = HOME_PATH / "assets" / "pyrit_architecture.png"
    resized = Image.open(sample_image).resize((1280, 720)).convert("RGB")

    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)  # noqa: SIM115
    resized.save(tmp, format="JPEG")
    tmp.close()
    image_path = tmp.name

    # Use the image for image-to-video
    conversation_id = str(uuid.uuid4())
    text_piece = MessagePiece(
        role="user",
        original_value="Animate this image with gentle motion",
        converted_value="Animate this image with gentle motion",
        conversation_id=conversation_id,
    )
    image_piece = MessagePiece(
        role="user",
        original_value=image_path,
        converted_value=image_path,
        converted_value_data_type="image_path",
        conversation_id=conversation_id,
    )
    result = await target.send_prompt_async(message=Message([text_piece, image_piece]))
    assert len(result) == 1
    response_piece = result[0].message_pieces[0]
    assert response_piece.response_error == "none", f"Image-to-video failed: {response_piece.converted_value}"

    video_path = Path(response_piece.converted_value)
    assert video_path.exists(), f"Video file not found: {video_path}"
    assert video_path.is_file()


##################################################
# Optional tests - not run in pipeline, only locally
# Need RUN_ALL_TESTS=true environment variable to run
###################################################


@pytest.mark.run_only_if_all_tests
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "api_key", "model_name"),
    [
        ("GROQ_ENDPOINT", "GROQ_KEY", "GROQ_LLAMA_MODEL"),
        ("OPEN_ROUTER_ENDPOINT", "OPEN_ROUTER_KEY", "OPEN_ROUTER_CLAUDE_MODEL"),
        ("OLLAMA_CHAT_ENDPOINT", "", "OLLAMA_MODEL"),
    ],
)
async def test_connect_non_required_openai_text_targets(sqlite_instance, endpoint, api_key, model_name):
    endpoint_value = _get_required_env_var(endpoint)
    # api_key can be empty string for OLLAMA
    api_key_value = os.getenv(api_key) if api_key else ""
    model_name_value = _get_required_env_var(model_name)

    target = OpenAIChatTarget(
        endpoint=endpoint_value,
        api_key=api_key_value,
        model_name=model_name_value,
    )

    await _assert_can_send_prompt(target)
