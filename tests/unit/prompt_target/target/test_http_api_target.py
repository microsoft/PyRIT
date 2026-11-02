# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from pyrit.models import Message, MessagePiece, RequestTraceContext
from pyrit.prompt_target.http_target.httpx_api_target import HTTPXAPITarget


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
    mock_response = httpx.Response(200, content=b'{"message": "File uploaded successfully", "filename": "mock.pdf"}')
    mock_request.return_value = mock_response

    # Create HTTPXAPITarget without passing a transport.
    target = HTTPXAPITarget(
        http_url="http://example.com/upload/",
        method="POST",
        file_path=file_path,
        allowed_upload_directory=Path(file_path).parent,
        timeout=180,
    )
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
        allowed_upload_directory=Path(file_path).parent,
        params={"alpha": "1"},
        timeout=180,
    )
    with pytest.warns(DeprecationWarning, match="implicit text-path uploads"):
        await target.send_prompt_async(message=message)

    assert mock_request.call_args.kwargs["params"] == {"alpha": "1"}

    os.unlink(file_path)


@patch("httpx.AsyncClient.request")
async def test_send_prompt_async_no_file(mock_request, patch_central_database):
    # Create a MessagePiece with converted_value that does not point to a valid file.
    message_piece = MessagePiece(role="user", original_value="mock", converted_value="non_existent_file.pdf")
    message = Message(message_pieces=[message_piece])

    # Mock a response simulating a standard API (non-file).
    mock_response = httpx.Response(200, content=b'{"status": "ok", "data": "Sample JSON response"}')
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


@patch("httpx.AsyncClient.request")
async def test_send_prompt_async_stores_decoded_text(mock_request, patch_central_database):
    message = Message(message_pieces=[MessagePiece(role="user", original_value="mock", converted_value="hello")])
    body = "R\u00e9ponse \u2014 \U0001f600"
    mock_request.return_value = httpx.Response(200, content=body.encode("utf-8"))

    target = HTTPXAPITarget(http_url="http://example.com/data/", method="POST", timeout=180)
    response = await target.send_prompt_async(message=message)

    assert response[0].get_value() == body


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

    assert RequestTraceContext.from_metadata(message_piece.prompt_metadata) is None
    mock_request.assert_called_once_with(
        method="POST",
        url="http://example.com/data/",
        headers={},
        params={"alpha": "1"},
        json={"payload": "value"},
        data=None,
        follow_redirects=True,
    )


@patch("httpx.AsyncClient.request")
async def test_send_prompt_async_follows_redirects_when_enabled(mock_request, patch_central_database):
    message_piece = MessagePiece(role="user", original_value="prompt", converted_value="prompt")
    message = Message(message_pieces=[message_piece])
    mock_response = MagicMock()
    mock_response.content = b'{"status": "ok"}'
    mock_request.return_value = mock_response
    target = HTTPXAPITarget(
        http_url="http://example.com/data/",
        method="POST",
        follow_redirects=True,
    )

    await target.send_prompt_async(message=message)

    assert mock_request.call_args.kwargs["follow_redirects"] is True


@patch("httpx.AsyncClient.request")
async def test_send_prompt_async_missing_explicit_file_path_raises(mock_request, patch_central_database, tmp_path):
    message_piece = MessagePiece(role="user", original_value="mock", converted_value="trigger")
    message = Message(message_pieces=[message_piece])
    missing_file = tmp_path / "missing.pdf"

    target = HTTPXAPITarget(
        http_url="http://example.com/upload/",
        method="POST",
        file_path=str(missing_file),
        allowed_upload_directory=tmp_path,
        timeout=180,
    )

    with pytest.raises(FileNotFoundError, match="File not found"):
        await target.send_prompt_async(message=message)

    mock_request.assert_not_called()


async def test_send_prompt_async_validation(patch_central_database):
    # Creating a Message with no pieces raises immediately
    with pytest.raises(ValueError, match="must have at least one message piece"):
        Message(message_pieces=[])


def test_default_configuration_supports_file_path_input_modalities():
    # A file-upload target must accept text plus every file-path data type a converter can emit
    # (e.g. PDFConverter emits "binary_path"), otherwise real converter output is rejected.
    modalities = HTTPXAPITarget._DEFAULT_CONFIGURATION.capabilities.input_modalities
    supported_types = {data_type for combo in modalities for data_type in combo}
    assert {"text", "image_path", "audio_path", "video_path", "binary_path"} <= supported_types


@patch("httpx.AsyncClient.request")
async def test_send_prompt_async_binary_path_upload(mock_request, patch_central_database):
    # Mirrors the real converter path: PDFConverter output arrives as a "binary_path" piece.
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(b"%PDF-1.4 mock binary content")
        tmp.flush()
        file_path = tmp.name

    message_piece = MessagePiece(
        role="user",
        original_value=file_path,
        original_value_data_type="binary_path",
        converted_value=file_path,
        converted_value_data_type="binary_path",
    )
    message = Message(message_pieces=[message_piece])

    mock_response = MagicMock()
    mock_response.content = b'{"message": "File uploaded successfully", "filename": "mock.pdf"}'
    mock_request.return_value = mock_response

    target = HTTPXAPITarget(
        http_url="http://example.com/upload/",
        method="POST",
        allowed_upload_directory=Path(file_path).parent,
        timeout=180,
    )

    # Must not raise "This target supports only the following data types: ..."
    response = await target.send_prompt_async(message=message)

    assert len(response) == 1
    mock_request.assert_called_once()
    # The multipart upload path was taken with the file's basename.
    files = mock_request.call_args.kwargs["files"]
    assert files["file"][0] == os.path.basename(file_path)

    os.unlink(file_path)


@patch("httpx.AsyncClient.request")
async def test_send_prompt_async_file_upload_without_allowed_directory_warns(
    mock_request, patch_central_database, tmp_path
):
    file_path = tmp_path / "document.pdf"
    file_path.write_bytes(b"content")
    message_piece = MessagePiece(role="user", original_value=str(file_path), converted_value=str(file_path))
    message = Message(message_pieces=[message_piece])
    mock_response = MagicMock()
    mock_response.content = b"uploaded"
    mock_request.return_value = mock_response
    target = HTTPXAPITarget(
        http_url="http://example.com/upload/",
        method="POST",
        follow_redirects=True,
    )

    with pytest.warns(DeprecationWarning) as warning_records:
        await target.send_prompt_async(message=message)

    warning_messages = [str(record.message) for record in warning_records]
    assert any("implicit text-path uploads" in message for message in warning_messages)
    assert any("without allowed_upload_directory" in message for message in warning_messages)
    mock_request.assert_called_once()


@patch("httpx.AsyncClient.request")
async def test_send_prompt_async_rejects_upload_outside_allowed_directory(
    mock_request, patch_central_database, tmp_path
):
    allowed_directory = tmp_path / "allowed"
    allowed_directory.mkdir()
    file_path = tmp_path / "outside.pdf"
    file_path.write_bytes(b"content")
    message_piece = MessagePiece(role="user", original_value="prompt", converted_value="prompt")
    message = Message(message_pieces=[message_piece])
    target = HTTPXAPITarget(
        http_url="http://example.com/upload/",
        method="POST",
        file_path=str(allowed_directory / ".." / file_path.name),
        allowed_upload_directory=allowed_directory,
    )

    with pytest.raises(ValueError, match="outside the allowed upload directory"):
        await target.send_prompt_async(message=message)

    mock_request.assert_not_called()


@patch("httpx.AsyncClient.request")
async def test_send_prompt_async_rejects_symlink_escaping_allowed_directory(
    mock_request, patch_central_database, tmp_path
):
    allowed_directory = tmp_path / "allowed"
    allowed_directory.mkdir()
    outside_file = tmp_path / "secret.pdf"
    outside_file.write_bytes(b"content")
    link = allowed_directory / "link.pdf"
    try:
        link.symlink_to(outside_file)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Cannot create symlink in this environment: {exc}")
    message_piece = MessagePiece(role="user", original_value="prompt", converted_value="prompt")
    message = Message(message_pieces=[message_piece])
    target = HTTPXAPITarget(
        http_url="http://example.com/upload/",
        method="POST",
        file_path=str(link),
        allowed_upload_directory=allowed_directory,
    )

    with pytest.raises(ValueError, match="outside the allowed upload directory"):
        await target.send_prompt_async(message=message)

    mock_request.assert_not_called()


@patch("httpx.AsyncClient.request")
async def test_send_prompt_async_validates_path_before_file_exists(mock_request, patch_central_database, tmp_path):
    allowed_directory = tmp_path / "allowed"
    allowed_directory.mkdir()
    outside_path = tmp_path / "missing.pdf"
    message_piece = MessagePiece(
        role="user",
        original_value=str(outside_path),
        original_value_data_type="binary_path",
        converted_value=str(outside_path),
        converted_value_data_type="binary_path",
    )
    message = Message(message_pieces=[message_piece])
    target = HTTPXAPITarget(
        http_url="http://example.com/upload/",
        method="POST",
        allowed_upload_directory=allowed_directory,
        follow_redirects=True,
    )

    with pytest.raises(ValueError, match="outside the allowed upload directory"):
        await target.send_prompt_async(message=message)

    mock_request.assert_not_called()


@patch("httpx.AsyncClient.request")
async def test_send_prompt_async_validates_upload_method_after_path(mock_request, patch_central_database, tmp_path):
    file_path = tmp_path / "document.pdf"
    file_path.write_bytes(b"content")
    message_piece = MessagePiece(
        role="user",
        original_value=str(file_path),
        original_value_data_type="binary_path",
        converted_value=str(file_path),
        converted_value_data_type="binary_path",
    )
    message = Message(message_pieces=[message_piece])
    target = HTTPXAPITarget(
        http_url="http://example.com/upload/",
        method="GET",
        allowed_upload_directory=tmp_path,
        follow_redirects=True,
    )

    with pytest.raises(ValueError, match="File uploads are not allowed with HTTP method: GET"):
        await target.send_prompt_async(message=message)

    mock_request.assert_not_called()


def test_httpx_api_target_identifier_fields(patch_central_database):
    target = HTTPXAPITarget(
        http_url="https://example.com/api/v1",
        method="POST",
        http2=True,
        follow_redirects=False,
    )
    identifier = target.get_identifier()

    assert identifier.endpoint == "https://example.com/api/v1"
    assert identifier.params["method"] == "POST"
    assert identifier.params["follow_redirects"] is False
    assert identifier.params["http2"] is True
    assert target._destination_origin == ("https", "example.com", None)


def test_httpx_api_target_identifier_distinguishes_urls_and_methods(patch_central_database):
    first = HTTPXAPITarget(http_url="https://example.com/api/v1", method="POST")
    different_url = HTTPXAPITarget(http_url="https://different.org/api/v2", method="POST")
    different_method = HTTPXAPITarget(http_url="https://example.com/api/v1", method="GET")

    assert first.get_identifier().hash != different_url.get_identifier().hash
    assert first.get_identifier().hash != different_method.get_identifier().hash


def test_httpx_api_target_identifier_keeps_non_credential_names_containing_credential_substrings(
    patch_central_database,
):
    """Harmless names that merely contain a credential substring must keep their
    values so distinct URLs keep distinct identifiers (regression: `design`
    contains "sig" and `author` contains "auth", but neither is a credential)."""
    kept = [
        HTTPXAPITarget(http_url="https://example.test/api?design=alpha"),
        HTTPXAPITarget(http_url="https://example.test/api?design=beta"),
        HTTPXAPITarget(http_url="https://example.test/api?author=jane&model=gpt-4"),
        HTTPXAPITarget(http_url="https://example.test/api?assigned_model=m1"),
    ]
    assert [t.get_identifier().endpoint for t in kept] == [
        "https://example.test/api?design=alpha",
        "https://example.test/api?design=beta",
        "https://example.test/api?author=jane&model=gpt-4",
        "https://example.test/api?assigned_model=m1",
    ]

    # Distinct values under the same harmless name no longer collide.
    assert kept[0].get_identifier().hash != kept[1].get_identifier().hash

    # Compound credential names are still redacted at word boundaries.
    stripped = [
        HTTPXAPITarget(http_url="https://example.test/api?api_key=k"),
        HTTPXAPITarget(http_url="https://example.test/api?client_secret=s"),
        HTTPXAPITarget(http_url="https://example.test/api?api-token=t"),
        HTTPXAPITarget(http_url="https://example.test/api?access_token=t"),
    ]
    assert all(t.get_identifier().endpoint == "https://example.test/api" for t in stripped)

    # camelCase spellings carry the same credential words and must redact too
    # (regression: splitting only on punctuation treated `accessToken` as one
    # opaque word, which leaked its value into the identifier).
    for camel in ("accessToken", "clientSecret", "authToken", "apiKey", "APIKey"):
        target = HTTPXAPITarget(http_url=f"https://example.test/api?{camel}=dummy-value")
        dump = target.get_identifier().model_dump_json()
        assert target.get_identifier().endpoint == "https://example.test/api", camel
        assert "dummy-value" not in dump, camel


def test_httpx_api_target_identifier_strips_fragment_and_extra_credential_queries(patch_central_database):
    # A fragment is never part of the HTTP request target and can carry credentials.
    target = HTTPXAPITarget(http_url="https://example.test/api#access_token=dummy-token")
    identifier = target.get_identifier()
    assert identifier.endpoint == "https://example.test/api"
    assert "dummy-token" not in identifier.model_dump_json()
    assert target.http_url == "https://example.test/api#access_token=dummy-token"

    # Credential query names that bypass the original name filter.
    target = HTTPXAPITarget(
        http_url="https://example.test/api?jwt=dummy-token&client_assertion=dummy-assertion&model=gpt-4"
    )
    identifier = target.get_identifier()
    assert identifier.endpoint == "https://example.test/api?model=gpt-4"
    assert "dummy-token" not in identifier.model_dump_json()
    assert "dummy-assertion" not in identifier.model_dump_json()


async def test_httpx_api_target_identifier_matches_captured_request_url(patch_central_database):
    """The identifier endpoint must resolve exactly like the httpx send path.

    Uses the same URL-building semantics as httpx.AsyncClient: the base URL is
    given a trailing slash and the request path is appended with any leading
    slash stripped, so distinct base paths produce distinct endpoints instead
    of colliding the way URL.join resolves them.
    """
    captured_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    message = Message(message_pieces=[MessagePiece(role="user", original_value="mock")])

    targets = [
        HTTPXAPITarget(
            http_url="invoke",
            method="POST",
            base_url="https://example.test/alpha",
            transport=httpx.MockTransport(handler),
        ),
        HTTPXAPITarget(
            http_url="invoke",
            method="POST",
            base_url="https://example.test/beta",
            transport=httpx.MockTransport(handler),
        ),
        HTTPXAPITarget(
            http_url="/invoke",
            method="POST",
            base_url="https://example.test/alpha",
            transport=httpx.MockTransport(handler),
        ),
    ]

    for target in targets:
        await target.send_prompt_async(message=message)

    # httpx appends the request path to the base path (trailing slash added,
    # leading slash stripped), so both spellings of the same path agree.
    assert captured_urls == [
        "https://example.test/alpha/invoke",
        "https://example.test/beta/invoke",
        "https://example.test/alpha/invoke",
    ]

    # The identifier endpoints match the URLs the client actually requested,
    # so distinct base paths no longer collide.
    endpoints = [target.get_identifier().endpoint for target in targets]
    assert endpoints == captured_urls
    assert targets[0].get_identifier().hash != targets[1].get_identifier().hash


def test_httpx_api_target_identifier_redacts_credentials(patch_central_database):
    url_with_creds = "https://user:fake-password@example.test/api?api_key=fake-token&model=gpt-4&version=1"
    target = HTTPXAPITarget(http_url=url_with_creds, method="POST")

    # Full URL is preserved for actual requests
    assert target.http_url == url_with_creds

    # Credentials must not appear in identifier or serialized metadata
    identifier = target.get_identifier()
    assert identifier.endpoint == "https://example.test/api?model=gpt-4&version=1"
    assert target._destination_origin == ("https", "example.test", None)

    dump = identifier.model_dump_json()
    assert "fake-password" not in dump
    assert "fake-token" not in dump
    assert "user" not in dump


def test_httpx_api_target_identifier_resolves_base_url(patch_central_database):
    target_a = HTTPXAPITarget(http_url="invoke", base_url="https://a.example.test/api/")
    target_b = HTTPXAPITarget(http_url="invoke", base_url="https://b.example.test/api/")

    assert target_a.get_identifier().endpoint == "https://a.example.test/api/invoke"
    assert target_b.get_identifier().endpoint == "https://b.example.test/api/invoke"
    assert target_a._destination_origin == ("https", "a.example.test", None)
    assert target_b._destination_origin == ("https", "b.example.test", None)

    assert target_a.get_identifier().hash != target_b.get_identifier().hash


def test_httpx_api_target_identifier_redacts_base_url_credentials(patch_central_database):
    target = HTTPXAPITarget(
        http_url="invoke?token=secret",
        base_url="https://admin:pass@example.test/api/",
    )

    identifier = target.get_identifier()
    assert identifier.endpoint == "https://example.test/api/invoke"
    assert "pass" not in identifier.model_dump_json()
    assert "secret" not in identifier.model_dump_json()
    assert "admin" not in identifier.model_dump_json()
