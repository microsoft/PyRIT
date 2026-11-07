# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Validate stored provider output according to its replayed representation."""

import uuid
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock, patch

import pytest
from openai.types.chat import ChatCompletion
from openai.types.responses import Response
from unit.mocks import openai_chat_response_json_dict, openai_response_json_dict

from pyrit.memory import CentralMemory
from pyrit.models import Message, MessagePiece
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import (
    LiteLLMChatTarget,
    OpenAIChatAudioConfig,
    OpenAIChatTarget,
    OpenAIResponseTarget,
    PromptTarget,
    RoundRobinTarget,
)

pytestmark = pytest.mark.usefixtures("patch_central_database")


def _chat_response(*, refusal: str | None = None) -> ChatCompletion:
    data = openai_chat_response_json_dict()
    data["created"] = 0
    data["choices"][0]["message"].update(content=None if refusal else "Answer", refusal=refusal)
    return ChatCompletion.model_validate(data)


def _responses_response(*, refusal: str | None = None, reasoning: bool = False) -> Response:
    data = openai_response_json_dict()
    data.update(created_at=0, parallel_tool_calls=True, tool_choice="auto", tools=[])
    data["output"][0]["status"] = "completed"
    data["output"][0]["content"] = (
        [{"type": "refusal", "refusal": refusal}]
        if refusal
        else [{"type": "output_text", "text": "Answer", "annotations": []}]
    )
    if reasoning:
        data["output"].insert(
            0,
            {
                "type": "reasoning",
                "id": "rs_history",
                "summary": [{"type": "summary_text", "text": "Stored reasoning"}],
            },
        )
    return Response.model_validate(data)


def _target(*, adapter: str, provider: AsyncMock, stack: ExitStack) -> PromptTarget:
    if adapter == "litellm":
        stack.enter_context(
            patch.object(LiteLLMChatTarget, "_import_litellm", return_value=SimpleNamespace(acompletion=provider))
        )
        return LiteLLMChatTarget(model_name="openai/gpt-5", underlying_model="gpt-5", api_key="test-key")
    kwargs = {
        "model_name": "test-deployment",
        "underlying_model": "gpt-5",
        "endpoint": "https://mock.example.com/",
        "api_key": "test-key",
    }
    if adapter == "responses":
        target = OpenAIResponseTarget(**kwargs)
        stack.enter_context(patch.object(target._client.responses, "create", provider))
    else:
        target = OpenAIChatTarget(**kwargs)
        stack.enter_context(patch.object(target._client.chat.completions, "create", provider))
    return target


@pytest.mark.parametrize("adapter", ["chat", "litellm", "responses"])
@pytest.mark.parametrize("wrapped", [False, True])
async def test_public_follow_up_replays_persisted_refusal_as_text_async(adapter: str, wrapped: bool) -> None:
    refusal = "I can't help with that request."
    first = _responses_response(refusal=refusal) if adapter == "responses" else _chat_response(refusal=refusal)
    second = _responses_response() if adapter == "responses" else _chat_response()
    provider = AsyncMock(side_effect=[first, second])
    conversation_id = str(uuid.uuid4())
    normalizer = PromptNormalizer()
    with ExitStack() as stack:
        target = _target(adapter=adapter, provider=provider, stack=stack)
        if wrapped:
            target = RoundRobinTarget(targets=[target, _target(adapter=adapter, provider=provider, stack=stack)])
        response = await normalizer.send_prompt_async(
            message=Message.from_prompt(prompt="First request", role="user"),
            target=target,
            conversation_id=conversation_id,
        )
        assert response.get_piece().converted_value_data_type == "error"
        assert response.get_piece().structured_refusal == refusal
        follow_up = await normalizer.send_prompt_async(
            message=Message.from_prompt(prompt="Follow up", role="user"), target=target, conversation_id=conversation_id
        )

    assert provider.await_count == 2
    assert follow_up.get_value() == "Answer"
    payload = provider.await_args_list[1].kwargs
    messages = payload["input" if adapter == "responses" else "messages"]
    assert [message["role"] for message in messages] == ["user", "assistant", "user"]
    content = messages[1]["content"]
    wire_text = content if isinstance(content, str) else content[0]["text"]
    assert wire_text == (refusal if adapter == "responses" else response.get_piece().converted_value)
    if adapter == "responses":
        assert content[0]["type"] == "output_text"
    stored = await CentralMemory.get_memory_instance().get_message_pieces_async(conversation_id=conversation_id)
    assert [piece.converted_value_data_type for piece in stored] == ["text", "error", "text", "text"]
    assert stored[1].structured_refusal == refusal


@pytest.mark.parametrize("wrapped", [False, True])
async def test_public_follow_up_omits_persisted_reasoning_for_known_model_async(wrapped: bool) -> None:
    provider = AsyncMock(side_effect=[_responses_response(reasoning=True), _responses_response()])
    conversation_id = str(uuid.uuid4())
    normalizer = PromptNormalizer()
    with ExitStack() as stack:
        target = _target(adapter="responses", provider=provider, stack=stack)
        if wrapped:
            target = RoundRobinTarget(targets=[target, _target(adapter="responses", provider=provider, stack=stack)])
        assert "reasoning" not in target.capabilities.supported_input_modalities
        response = await normalizer.send_prompt_async(
            message=Message.from_prompt(prompt="First request", role="user"),
            target=target,
            conversation_id=conversation_id,
        )
        assert any(piece.converted_value_data_type == "reasoning" for piece in response.message_pieces)
        follow_up = await normalizer.send_prompt_async(
            message=Message.from_prompt(prompt="Follow up", role="user"), target=target, conversation_id=conversation_id
        )

    assert provider.await_count == 2
    assert follow_up.get_value() == "Answer"
    messages = provider.await_args_list[1].kwargs["input"]
    assert [message["role"] for message in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"] == [{"type": "output_text", "text": "Answer"}]
    stored = await CentralMemory.get_memory_instance().get_message_pieces_async(conversation_id=conversation_id)
    assert sum(piece.converted_value_data_type == "reasoning" for piece in stored) == 1


@pytest.mark.parametrize("adapter", ["chat", "litellm"])
async def test_public_follow_up_replays_content_filter_error_as_text_async(adapter: str) -> None:
    blocked = _chat_response()
    blocked.choices[0].finish_reason = "content_filter"
    provider = AsyncMock(side_effect=[blocked, _chat_response()])
    conversation_id = str(uuid.uuid4())
    normalizer = PromptNormalizer()
    with ExitStack() as stack:
        target = _target(adapter=adapter, provider=provider, stack=stack)
        response = await normalizer.send_prompt_async(
            message=Message.from_prompt(prompt="First request", role="user"),
            target=target,
            conversation_id=conversation_id,
        )
        assert response.get_piece().converted_value_data_type == "error"
        assert response.get_piece().structured_refusal is None
        await normalizer.send_prompt_async(
            message=Message.from_prompt(prompt="Follow up", role="user"), target=target, conversation_id=conversation_id
        )
    assert provider.await_count == 2
    content = provider.await_args_list[1].kwargs["messages"][1]["content"]
    assert (content if isinstance(content, str) else content[0]["text"]) == response.get_piece().converted_value


@pytest.mark.parametrize("adapter", ["chat", "litellm", "responses"])
async def test_public_follow_up_rejects_unsupported_image_history_async(adapter: str, tmp_path: Path) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(b"image data")
    conversation_id = str(uuid.uuid4())
    memory = CentralMemory.get_memory_instance()
    await memory.add_message_to_memory_async(
        request=Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    conversation_id=conversation_id,
                    sequence=0,
                    original_value=str(image),
                    original_value_data_type="image_path",
                )
            ]
        )
    )
    provider = AsyncMock()
    with ExitStack() as stack:
        target = _target(adapter=adapter, provider=provider, stack=stack)
        target.apply_capabilities(
            capabilities=target.capabilities.model_copy(update={"input_modalities": frozenset({frozenset({"text"})})})
        )
        with pytest.raises(ValueError, match="Received: image_path"):
            await target.send_prompt_async(
                message=Message(
                    message_pieces=[
                        MessagePiece(role="user", conversation_id=conversation_id, original_value="Follow up")
                    ]
                )
            )
    provider.assert_not_awaited()


async def test_responses_does_not_exempt_unstructured_error_history_async() -> None:
    conversation_id = str(uuid.uuid4())
    await CentralMemory.get_memory_instance().add_message_to_memory_async(
        request=Message(
            message_pieces=[
                MessagePiece(
                    role="assistant",
                    conversation_id=conversation_id,
                    sequence=0,
                    original_value="Not a structured refusal",
                    original_value_data_type="error",
                    response_error="blocked",
                )
            ]
        )
    )
    provider = AsyncMock()
    with ExitStack() as stack:
        target = _target(adapter="responses", provider=provider, stack=stack)
        with pytest.raises(ValueError, match="Received: error"):
            await target.send_prompt_async(
                message=Message(
                    message_pieces=[
                        MessagePiece(role="user", conversation_id=conversation_id, original_value="Follow up")
                    ]
                )
            )
    provider.assert_not_awaited()


@pytest.mark.parametrize("adapter", ["chat", "litellm", "responses"])
@pytest.mark.parametrize("role", ["user", "assistant"])
@pytest.mark.parametrize("data_type", ["error", "reasoning", "image_path"])
async def test_current_request_still_rejects_unsupported_modalities_async(
    adapter: str, role: Literal["user", "assistant"], data_type: Literal["error", "reasoning", "image_path"]
) -> None:
    provider = AsyncMock()
    with ExitStack() as stack:
        target = _target(adapter=adapter, provider=provider, stack=stack)
        target.apply_capabilities(
            capabilities=target.capabilities.model_copy(update={"input_modalities": frozenset({frozenset({"text"})})})
        )
        piece = MessagePiece(
            role=role,
            original_value="unsupported",
            original_value_data_type=data_type,
            response_error="blocked" if data_type == "error" else "none",
            prompt_metadata={MessagePiece.STRUCTURED_REFUSAL_METADATA_KEY: "Refusal"} if data_type == "error" else {},
        )
        with pytest.raises(ValueError, match=f"Received: {data_type}"):
            await target.send_prompt_async(message=Message(message_pieces=[piece]))
    provider.assert_not_awaited()


@pytest.mark.parametrize("drop_audio", [True, False])
async def test_round_robin_validates_history_for_every_possible_receiver_async(drop_audio: bool) -> None:
    conversation_id = str(uuid.uuid4())
    await CentralMemory.get_memory_instance().add_message_to_memory_async(
        request=Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    conversation_id=conversation_id,
                    original_value="audio.wav",
                    original_value_data_type="audio_path",
                ),
                MessagePiece(role="user", conversation_id=conversation_id, original_value="Transcript"),
            ]
        )
    )
    provider = AsyncMock(return_value=_chat_response())
    with ExitStack() as stack:
        targets = [
            OpenAIChatTarget(
                model_name="test-deployment",
                underlying_model="gpt-5",
                endpoint="https://mock.example.com/",
                api_key="test-key",
                audio_response_config=OpenAIChatAudioConfig(voice="alloy", prefer_transcript_for_history=preference),
            )
            for preference in [True, drop_audio]
        ]
        for child in targets:
            stack.enter_context(patch.object(child._client.chat.completions, "create", provider))
        target = RoundRobinTarget(targets=targets)
        request = Message(
            message_pieces=[MessagePiece(role="user", conversation_id=conversation_id, original_value="Follow up")]
        )
        if drop_audio:
            response = await target.send_prompt_async(message=request)
            assert response[0].get_value() == "Answer"
            assert provider.await_args.kwargs["messages"][0]["content"] == [{"type": "text", "text": "Transcript"}]
            provider.assert_awaited_once()
        else:
            with pytest.raises(ValueError, match="Received: audio_path"):
                await target.send_prompt_async(message=request)
            provider.assert_not_awaited()
