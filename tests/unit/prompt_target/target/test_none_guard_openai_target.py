# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import BadRequestError, ContentFilterFinishReasonError

from pyrit.models import Message
from pyrit.prompt_target import OpenAIChatTarget


def test_client_property_raises_when_async_client_none(patch_central_database):
    target = OpenAIChatTarget(endpoint="https://test.openai.com", api_key="test", model_name="gpt-4")
    target._async_client = None
    with pytest.raises(RuntimeError, match="AsyncOpenAI client is not initialized"):
        _ = target._client


@pytest.mark.asyncio
async def test_handle_openai_request_raises_when_no_message_pieces(patch_central_database):
    """The try-block guard (line 442) raises when request has no message_pieces."""
    target = OpenAIChatTarget(endpoint="https://test.openai.com", api_key="test", model_name="gpt-4")
    empty_request = MagicMock(spec=Message)
    empty_request.message_pieces = []

    api_call = AsyncMock(return_value=MagicMock())

    with pytest.raises(ValueError, match="No message pieces in request"):
        await target._handle_openai_request(api_call=api_call, request=empty_request)


@pytest.mark.asyncio
async def test_handle_openai_request_content_filter_error_raises_when_no_message_pieces(patch_central_database):
    """The ContentFilterFinishReasonError handler (line 470) raises when request has no pieces."""
    target = OpenAIChatTarget(endpoint="https://test.openai.com", api_key="test", model_name="gpt-4")
    empty_request = MagicMock(spec=Message)
    empty_request.message_pieces = []

    api_call = AsyncMock(
        side_effect=ContentFilterFinishReasonError(),
    )

    with pytest.raises(ValueError, match="No message pieces in request"):
        await target._handle_openai_request(api_call=api_call, request=empty_request)


@pytest.mark.asyncio
async def test_handle_openai_request_bad_request_error_raises_when_no_message_pieces(patch_central_database):
    """The BadRequestError handler (line 490) raises when request has no pieces."""
    target = OpenAIChatTarget(endpoint="https://test.openai.com", api_key="test", model_name="gpt-4")
    empty_request = MagicMock(spec=Message)
    empty_request.message_pieces = []

    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.json.return_value = {"error": {"message": "bad request", "code": "invalid_request"}}
    mock_response.headers = {}

    api_call = AsyncMock(
        side_effect=BadRequestError(
            message="bad request",
            response=mock_response,
            body={"error": {"message": "bad request", "code": "invalid_request"}},
        ),
    )

    with pytest.raises(ValueError, match="No message pieces in request"):
        await target._handle_openai_request(api_call=api_call, request=empty_request)
