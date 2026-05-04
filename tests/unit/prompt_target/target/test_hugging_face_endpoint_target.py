# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target.hugging_face.hugging_face_endpoint_target import (
    HuggingFaceEndpointTarget,
)

# HuggingFaceEndpointTarget emits a DeprecationWarning on construction
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def hugging_face_endpoint_target(patch_central_database) -> HuggingFaceEndpointTarget:
    return HuggingFaceEndpointTarget(
        hf_token="test_token",
        endpoint="https://api-inference.huggingface.co/models/test-model",
        model_id="test-model",
    )


def test_hugging_face_endpoint_initializes(hugging_face_endpoint_target: HuggingFaceEndpointTarget):
    assert hugging_face_endpoint_target


def test_hugging_face_endpoint_sets_endpoint_and_rate_limit():
    target = HuggingFaceEndpointTarget(
        hf_token="test_token",
        endpoint="https://api-inference.huggingface.co/models/test-model",
        model_id="test-model",
        max_requests_per_minute=30,
    )
    identifier = target.get_identifier()
    assert identifier.params["endpoint"] == "https://api-inference.huggingface.co/models/test-model"
    assert target._max_requests_per_minute == 30


def test_invalid_temperature_too_low_raises(patch_central_database):
    with pytest.raises(Exception, match="temperature must be between 0 and 2"):
        HuggingFaceEndpointTarget(
            hf_token="test_token",
            endpoint="https://api-inference.huggingface.co/models/test-model",
            model_id="test-model",
            temperature=-0.1,
        )


def test_invalid_temperature_too_high_raises(patch_central_database):
    with pytest.raises(Exception, match="temperature must be between 0 and 2"):
        HuggingFaceEndpointTarget(
            hf_token="test_token",
            endpoint="https://api-inference.huggingface.co/models/test-model",
            model_id="test-model",
            temperature=2.1,
        )


def test_invalid_top_p_too_low_raises(patch_central_database):
    with pytest.raises(Exception, match="top_p must be between 0 and 1"):
        HuggingFaceEndpointTarget(
            hf_token="test_token",
            endpoint="https://api-inference.huggingface.co/models/test-model",
            model_id="test-model",
            top_p=-0.1,
        )


def test_invalid_top_p_too_high_raises(patch_central_database):
    with pytest.raises(Exception, match="top_p must be between 0 and 1"):
        HuggingFaceEndpointTarget(
            hf_token="test_token",
            endpoint="https://api-inference.huggingface.co/models/test-model",
            model_id="test-model",
            top_p=1.1,
        )


def test_valid_temperature_and_top_p(patch_central_database):
    # Should not raise any exceptions
    target = HuggingFaceEndpointTarget(
        hf_token="test_token",
        endpoint="https://api-inference.huggingface.co/models/test-model",
        model_id="test-model",
        temperature=1.5,
        top_p=0.9,
    )
    assert target._temperature == 1.5
    assert target._top_p == 0.9


def test_identifier_includes_generation_params():
    """New generation params (top_k, do_sample, repetition_penalty) appear in the identifier."""
    target = HuggingFaceEndpointTarget(
        hf_token="test_token",
        endpoint="https://api-inference.huggingface.co/models/test-model",
        model_id="test-model",
        top_k=40,
        do_sample=True,
        repetition_penalty=1.2,
    )
    identifier = target.get_identifier()
    assert identifier.params["top_k"] == 40
    assert identifier.params["do_sample"] is True
    assert identifier.params["repetition_penalty"] == 1.2


def test_identifier_excludes_none_generation_params():
    """None-valued generation params are excluded from the identifier."""
    target = HuggingFaceEndpointTarget(
        hf_token="test_token",
        endpoint="https://api-inference.huggingface.co/models/test-model",
        model_id="test-model",
    )
    identifier = target.get_identifier()
    assert "top_k" not in identifier.params
    assert "do_sample" not in identifier.params
    assert "repetition_penalty" not in identifier.params


def test_sampling_params_without_do_sample_warns():
    """Setting temperature != 1.0 without do_sample=True emits a warning."""
    with pytest.warns(UserWarning, match="do_sample is not True"):
        HuggingFaceEndpointTarget(
            hf_token="test_token",
            endpoint="https://api-inference.huggingface.co/models/test-model",
            model_id="test-model",
            temperature=0.7,
        )


def test_sampling_params_with_do_sample_no_warning():
    """Setting temperature != 1.0 with do_sample=True does not warn."""
    import warnings as _warnings

    with _warnings.catch_warnings():
        _warnings.simplefilter("error", UserWarning)
        HuggingFaceEndpointTarget(
            hf_token="test_token",
            endpoint="https://api-inference.huggingface.co/models/test-model",
            model_id="test-model",
            temperature=0.7,
            do_sample=True,
        )


@pytest.mark.filterwarnings("default::DeprecationWarning")
def test_init_emits_deprecation_warning():
    """HuggingFaceEndpointTarget emits a DeprecationWarning on construction."""
    with pytest.warns(DeprecationWarning, match="deprecated and will be removed"):
        HuggingFaceEndpointTarget(
            hf_token="test_token",
            endpoint="https://api-inference.huggingface.co/models/test-model",
            model_id="test-model",
        )


def _make_user_message(text: str) -> Message:
    """Helper to create a single-piece user Message."""
    return Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value=text,
                converted_value=text,
                converted_value_data_type="text",
            )
        ]
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
async def test_send_prompt_async_list_response():
    """Verify send_prompt_async handles a list response from the HF API."""
    target = HuggingFaceEndpointTarget(
        hf_token="test_token",
        endpoint="https://api-inference.huggingface.co/models/test-model",
        model_id="test-model",
    )

    mock_response = MagicMock()
    mock_response.json.return_value = [{"generated_text": "Hello from HF"}]

    with patch(
        "pyrit.prompt_target.hugging_face.hugging_face_endpoint_target.make_request_and_raise_if_error_async",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        message = _make_user_message("test prompt")
        response = await target.send_prompt_async(message=message)

    assert len(response) == 1
    assert response[0].message_pieces[0].original_value == "Hello from HF"


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
async def test_send_prompt_async_dict_response():
    """Verify send_prompt_async handles a dict response from the HF API."""
    target = HuggingFaceEndpointTarget(
        hf_token="test_token",
        endpoint="https://api-inference.huggingface.co/models/test-model",
        model_id="test-model",
    )

    mock_response = MagicMock()
    mock_response.json.return_value = {"generated_text": "Dict response"}

    with patch(
        "pyrit.prompt_target.hugging_face.hugging_face_endpoint_target.make_request_and_raise_if_error_async",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        message = _make_user_message("test prompt")
        response = await target.send_prompt_async(message=message)

    assert len(response) == 1
    assert response[0].message_pieces[0].original_value == "Dict response"


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
async def test_send_prompt_async_passes_optional_params_in_payload():
    """Verify optional generation params are included in the HTTP payload."""
    target = HuggingFaceEndpointTarget(
        hf_token="test_token",
        endpoint="https://api-inference.huggingface.co/models/test-model",
        model_id="test-model",
        top_k=40,
        do_sample=True,
        repetition_penalty=1.2,
    )

    mock_response = MagicMock()
    mock_response.json.return_value = [{"generated_text": "response"}]

    with patch(
        "pyrit.prompt_target.hugging_face.hugging_face_endpoint_target.make_request_and_raise_if_error_async",
        new_callable=AsyncMock,
        return_value=mock_response,
    ) as mock_request:
        message = _make_user_message("test prompt")
        await target.send_prompt_async(message=message)

    call_kwargs = mock_request.call_args[1]
    params = call_kwargs["request_body"]["parameters"]
    assert params["top_k"] == 40
    assert params["do_sample"] is True
    assert params["repetition_penalty"] == 1.2


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
async def test_send_prompt_async_omits_none_params_from_payload():
    """Verify None-valued optional params are not in the HTTP payload."""
    target = HuggingFaceEndpointTarget(
        hf_token="test_token",
        endpoint="https://api-inference.huggingface.co/models/test-model",
        model_id="test-model",
    )

    mock_response = MagicMock()
    mock_response.json.return_value = [{"generated_text": "response"}]

    with patch(
        "pyrit.prompt_target.hugging_face.hugging_face_endpoint_target.make_request_and_raise_if_error_async",
        new_callable=AsyncMock,
        return_value=mock_response,
    ) as mock_request:
        message = _make_user_message("test prompt")
        await target.send_prompt_async(message=message)

    call_kwargs = mock_request.call_args[1]
    params = call_kwargs["request_body"]["parameters"]
    assert "top_k" not in params
    assert "do_sample" not in params
    assert "repetition_penalty" not in params


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_central_database")
async def test_send_prompt_async_metadata_contains_model_id():
    """Verify prompt_metadata includes the model_id."""
    target = HuggingFaceEndpointTarget(
        hf_token="test_token",
        endpoint="https://api-inference.huggingface.co/models/test-model",
        model_id="test-model",
    )

    mock_response = MagicMock()
    mock_response.json.return_value = [{"generated_text": "response"}]

    with patch(
        "pyrit.prompt_target.hugging_face.hugging_face_endpoint_target.make_request_and_raise_if_error_async",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        message = _make_user_message("test prompt")
        response = await target.send_prompt_async(message=message)

    metadata = response[0].message_pieces[0].prompt_metadata
    assert metadata["model_id"] == "test-model"


def test_validate_request_rejects_multiple_pieces():
    """Verify _validate_request raises for messages with multiple pieces."""
    target = HuggingFaceEndpointTarget(
        hf_token="test_token",
        endpoint="https://api-inference.huggingface.co/models/test-model",
        model_id="test-model",
    )

    piece1 = MessagePiece(
        role="user",
        original_value="first",
        converted_value="first",
        converted_value_data_type="text",
        conversation_id="conv1",
    )
    piece2 = MessagePiece(
        role="user",
        original_value="second",
        converted_value="second",
        converted_value_data_type="text",
        conversation_id="conv1",
    )
    message = Message(message_pieces=[piece1, piece2])

    with pytest.raises(ValueError, match="single message piece"):
        target._validate_request(normalized_conversation=[message])
