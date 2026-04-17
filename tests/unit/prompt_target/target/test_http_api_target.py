# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target.http_target.httpx_api_target import HTTPXAPITarget


@pytest.mark.asyncio
@patch("httpx.AsyncClient.request")
async def test_send_prompt_async_file_upload(mock_request, patch_central_database):
    # Create a temporary file to simulate a PDF.
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(b"This is a mock PDF content")
        tmp.flush()
        file_path = tmp.name

    # Create a MessagePiece with converted_value set to the temporary file path.
    message_piece = MessagePiece(role="user", original_value="mock", converted_value=file_path)
    message = Message(message_pieces=[message_piece])

    # Mock a response simulating a file upload.
    mock_response = MagicMock()
    mock_response.content = b'{"message": "File uploaded successfully", "filename": "mock.pdf"}'
    mock_request.return_value = mock_response

    # Create HTTPXAPITarget without passing a transport.
    target = HTTPXAPITarget(http_url="http://example.com/upload/", method="POST", timeout=180)
    response = await target.send_prompt_async(message=message)

    # Our mock transport returns a JSON string containing "File uploaded successfully".
    assert len(response) == 1
    response_text = (
        str(response[0].message_pieces[0].converted_value)
        if response[0].message_pieces[0].converted_value
        else str(response[0])
    )
    assert "File uploaded successfully" in response_text

    # Clean up the temporary file.
    os.unlink(file_path)


@pytest.mark.asyncio
@patch("httpx.AsyncClient.request")
async def test_send_prompt_async_file_upload_preserves_query_params(mock_request, patch_central_database):
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(b"This is a mock PDF content")
        tmp.flush()
        file_path = tmp.name

    message_piece = MessagePiece(role="user", original_value="mock", converted_value=file_path)
    message = Message(message_pieces=[message_piece])

    mock_response = MagicMock()
    mock_response.content = b'{"message": "File uploaded successfully"}'
    mock_request.return_value = mock_response

    target = HTTPXAPITarget(
        http_url="http://example.com/upload/",
        method="POST",
        params={"alpha": "1"},
        timeout=180,
    )
    await target.send_prompt_async(message=message)

    assert mock_request.call_args.kwargs["params"] == {"alpha": "1"}

    os.unlink(file_path)


@pytest.mark.asyncio
@patch("httpx.AsyncClient.request")
async def test_send_prompt_async_no_file(mock_request, patch_central_database):
    # Create a MessagePiece with converted_value that does not point to a valid file.
    message_piece = MessagePiece(role="user", original_value="mock", converted_value="non_existent_file.pdf")
    message = Message(message_pieces=[message_piece])

    # Mock a response simulating a standard API (non-file).
    mock_response = MagicMock()
    mock_response.content = b'{"status": "ok", "data": "Sample JSON response"}'
    mock_request.return_value = mock_response

    target = HTTPXAPITarget(http_url="http://example.com/data/", method="POST", timeout=180)
    response = await target.send_prompt_async(message=message)

    # The mock transport returns a JSON string containing "Sample JSON response".
    assert len(response) == 1
    response_text = (
        str(response[0].message_pieces[0].converted_value)
        if response[0].message_pieces[0].converted_value
        else str(response[0])
    )
    assert "Sample JSON response" in response_text


@pytest.mark.asyncio
@patch("httpx.AsyncClient.request")
async def test_send_prompt_async_preserves_query_params_for_post(mock_request, patch_central_database):
    message_piece = MessagePiece(role="user", original_value="mock", converted_value="non_existent_file.pdf")
    message = Message(message_pieces=[message_piece])

    mock_response = MagicMock()
    mock_response.content = b'{"status": "ok"}'
    mock_request.return_value = mock_response

    target = HTTPXAPITarget(
        http_url="http://example.com/data/",
        method="POST",
        params={"alpha": "1"},
        json_data={"payload": "value"},
        timeout=180,
    )
    await target.send_prompt_async(message=message)

    mock_request.assert_called_once_with(
        method="POST",
        url="http://example.com/data/",
        headers={},
        params={"alpha": "1"},
        json={"payload": "value"},
        data=None,
        follow_redirects=True,
    )


@pytest.mark.asyncio
@patch("httpx.AsyncClient.request")
async def test_send_prompt_async_missing_explicit_file_path_raises(mock_request, patch_central_database):
    message_piece = MessagePiece(role="user", original_value="mock", converted_value="trigger")
    message = Message(message_pieces=[message_piece])

    target = HTTPXAPITarget(
        http_url="http://example.com/upload/",
        method="POST",
        file_path="/definitely/missing/file.pdf",
        timeout=180,
    )

    with pytest.raises(FileNotFoundError, match="File not found"):
        await target.send_prompt_async(message=message)

    mock_request.assert_not_called()


@pytest.mark.asyncio
async def test_send_prompt_async_validation(patch_central_database):
    # Creating a Message with no pieces raises immediately
    with pytest.raises(ValueError, match="must have at least one message piece"):
        Message(message_pieces=[])
