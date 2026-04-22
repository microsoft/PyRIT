# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

from pyrit.auth import CopilotAuthenticator
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import WebSocketCopilotTarget
from pyrit.prompt_target.websocket_copilot_target import CopilotMessageType


@pytest.fixture
def mock_authenticator():
    token_payload = {"tid": "test_tenant_id", "oid": "test_object_id", "exp": 9999999999}
    mock_token = jwt.encode(token_payload, "a]T3!s$ecReT#kEy_4_tEst!ng@2024+", algorithm="HS256")
    if isinstance(mock_token, bytes):
        mock_token = mock_token.decode("utf-8")
    authenticator = MagicMock(spec=CopilotAuthenticator)
    authenticator.get_token = AsyncMock(return_value=mock_token)
    authenticator.get_claims = AsyncMock(return_value=token_payload)
    return authenticator


@pytest.fixture
def mock_copilot_target(mock_authenticator) -> WebSocketCopilotTarget:
    return WebSocketCopilotTarget(authenticator=mock_authenticator)


@pytest.fixture
def mock_websocket():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.__aenter__ = AsyncMock(return_value=ws)
    ws.__aexit__ = AsyncMock(return_value=None)
    return ws


def make_mock_async_client(mock_response):
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


@pytest.fixture
def make_message_piece():
    def _make(
        value: str,
        data_type: str = "text",
        role: str = "user",
        conversation_id: str = "conv_456",
    ) -> MessagePiece:
        return MessagePiece(
            role=role,
            original_value=value,
            converted_value=value,
            conversation_id=conversation_id,
            original_value_data_type=data_type,
            converted_value_data_type=data_type,
        )

    return _make


@pytest.fixture
def make_annotation():
    def _make(
        doc_id: str,
        file_name: str,
        file_type: str = None,
    ) -> dict:
        if file_type is None:
            file_type = file_name.split(".")[-1].lower() if "." in file_name else "png"
        return {
            "id": doc_id,
            "messageAnnotationMetadata": {
                "@type": "File",
                "annotationType": "File",
                "fileType": file_type,
                "fileName": file_name,
            },
            "messageAnnotationType": "ImageFile",
        }

    return _make


@pytest.fixture
def sample_text_pieces(make_message_piece):
    return [make_message_piece("Hello")]


@pytest.fixture
def sample_image_pieces(make_message_piece):
    return [make_message_piece("/path/to/image.png", data_type="image_path")]


@pytest.fixture
def sample_mixed_pieces(make_message_piece):
    return [
        make_message_piece("Describe this image"),
        make_message_piece("/path/to/image1.png", data_type="image_path"),
        make_message_piece("/path/to/image2.jpg", data_type="image_path"),
    ]


@pytest.fixture
def patch_convert_local_image_to_data_url():
    with patch(
        "pyrit.prompt_target.websocket_copilot_target.convert_local_image_to_data_url",
        new=AsyncMock(return_value="data:image/png;base64,abc123"),
    ):
        yield


@pytest.fixture
def mock_memory():
    memory = MagicMock()
    memory.get_conversation.return_value = []
    memory.add_message_to_memory = AsyncMock()
    return memory


@pytest.mark.usefixtures("patch_central_database")
class TestWebSocketCopilotTargetInit:
    def test_init_with_default_parameters(self):
        with patch("pyrit.prompt_target.websocket_copilot_target.CopilotAuthenticator") as mock_auth_class:
            mock_auth_instance = MagicMock(spec=CopilotAuthenticator)
            mock_auth_class.return_value = mock_auth_instance

            target = WebSocketCopilotTarget()

            mock_auth_class.assert_called_once()
            assert target._authenticator == mock_auth_instance
            assert target._response_timeout_seconds == WebSocketCopilotTarget.RESPONSE_TIMEOUT_SECONDS
            assert target._model_name == "copilot"
            assert target._endpoint == "wss://substrate.office.com/m365Copilot/Chathub"
            assert target._verbose is False
            assert target._max_requests_per_minute is None

    def test_init_with_custom_parameters(self, mock_authenticator):
        target = WebSocketCopilotTarget(
            authenticator=mock_authenticator,
            max_requests_per_minute=10,
            model_name="custom_copilot",
            response_timeout_seconds=120,
        )

        assert target._authenticator == mock_authenticator
        assert target._response_timeout_seconds == 120
        assert target._model_name == "custom_copilot"
        assert target._max_requests_per_minute == 10

    def test_init_with_invalid_response_timeout(self, mock_authenticator):
        for invalid_timeout in [0, -10, -1]:
            with pytest.raises(ValueError, match="response_timeout_seconds must be a positive integer."):
                WebSocketCopilotTarget(authenticator=mock_authenticator, response_timeout_seconds=invalid_timeout)


@pytest.mark.usefixtures("patch_central_database")
class TestDictToWebsocket:
    @pytest.mark.parametrize(
        "data,expected",
        [
            ({"key": "value"}, '{"key":"value"}\x1e'),
            ({"protocol": "json", "version": 1}, '{"protocol":"json","version":1}\x1e'),
            ({"outer": {"inner": "value"}}, '{"outer":{"inner":"value"}}\x1e'),
            ({"items": [1, 2, 3]}, '{"items":[1,2,3]}\x1e'),
        ],
    )
    def test_dict_to_websocket_converts_to_json_with_separator(self, data, expected):
        result = WebSocketCopilotTarget._dict_to_websocket(data)
        assert result == expected


@pytest.mark.usefixtures("patch_central_database")
class TestParseRawMessage:
    @pytest.mark.parametrize(
        "message,expected_types,expected_content",
        [
            ("", [CopilotMessageType.UNKNOWN], [""]),
            ("   \n\t  ", [CopilotMessageType.UNKNOWN], [""]),
            ("{}\x1e", [CopilotMessageType.UNKNOWN], [""]),
            ('{"type":6}\x1e', [CopilotMessageType.PING], [""]),
            (
                '{"type":1,"target":"update","arguments":[{"messages":[{"text":"Partial","author":"bot"}]}]}\x1e',
                [CopilotMessageType.PARTIAL_RESPONSE],
                [""],
            ),
            (
                '{"type":2,"item":{"result":{"message":"Final."}}}\x1e{"type":3,"invocationId":"0"}\x1e',
                [CopilotMessageType.FINAL_CONTENT, CopilotMessageType.STREAM_END],
                ["Final.", ""],
            ),
            (
                '{"type":3,"invocationId":"0"}\x1e',
                [CopilotMessageType.STREAM_END],
                [""],
            ),
        ],
    )
    def test_parse_raw_message_with_valid_data(self, message, expected_types, expected_content):
        result = WebSocketCopilotTarget._parse_raw_message(message)

        assert len(result) == len(expected_types)
        for i, expected_type in enumerate(expected_types):
            assert result[i][0] == expected_type
            assert result[i][1] == expected_content[i]

    def test_parse_final_message_without_content(self):
        with patch("pyrit.prompt_target.websocket_copilot_target.logger") as mock_logger:
            message = '{"type":2,"invocationId":"0"}\x1e'
            result = WebSocketCopilotTarget._parse_raw_message(message)

            assert len(result) == 1
            assert result[0][0] == CopilotMessageType.FINAL_CONTENT
            assert result[0][1] == ""

            mock_logger.warning.assert_called_with("FINAL_CONTENT received but no parseable content found.")
            mock_logger.debug.assert_called_with(f"Full raw message: {message[:-1]}")

    @pytest.mark.parametrize(
        "message",
        [
            '{"type":99,"data":"unknown"}\x1e',
            '{"data":"no type field"}\x1e',
            '{"invalid json structure\x1e',
        ],
    )
    def test_parse_unknown_or_invalid_messages(self, message):
        result = WebSocketCopilotTarget._parse_raw_message(message)
        assert len(result) == 1
        assert result[0][0] == CopilotMessageType.UNKNOWN
        assert result[0][1] == ""


@pytest.mark.usefixtures("patch_central_database")
class TestBuildWebsocketUrl:
    @pytest.mark.asyncio
    async def test_build_websocket_url_with_valid_token(self, mock_authenticator, mock_copilot_target):
        target = mock_copilot_target

        session_id = "test_session_id"
        copilot_conversation_id = "test_conversation_id"

        url = await target._build_websocket_url_async(
            session_id=session_id, copilot_conversation_id=copilot_conversation_id
        )
        expected_token = await mock_authenticator.get_token_async()

        assert url.startswith("wss://substrate.office.com/m365Copilot/Chathub/test_object_id@test_tenant_id?")
        assert f"X-SessionId={session_id}" in url
        assert f"ConversationId={copilot_conversation_id}" in url
        assert f"access_token={expected_token}" in url
        assert "ClientRequestId=" in url
        assert "source=%22officeweb%22" in url
        assert "scenario=OfficeWebIncludedCopilot" in url

    @pytest.mark.asyncio
    async def test_build_websocket_url_with_missing_ids(self, mock_authenticator, mock_copilot_target):
        for missing_id in ["tid", "oid"]:
            token_payload = {"tid": "test_tenant_id", "oid": "test_object_id", "exp": 9999999999}
            del token_payload[missing_id]

            mock_token = jwt.encode(token_payload, "a]T3!s$ecReT#kEy_4_tEst!ng@2024+", algorithm="HS256")
            if isinstance(mock_token, bytes):
                mock_token = mock_token.decode("utf-8")
            mock_authenticator.get_token = AsyncMock(return_value=mock_token)
            mock_authenticator.get_claims = AsyncMock(return_value=token_payload)

            target = mock_copilot_target
            with pytest.raises(ValueError, match="Failed to extract tenant_id \\(tid\\) or object_id \\(oid\\)"):
                await target._build_websocket_url_async(session_id="test", copilot_conversation_id="test")

    @pytest.mark.asyncio
    async def test_build_websocket_url_with_invalid_token(self, mock_authenticator, mock_copilot_target):
        mock_authenticator.get_token = AsyncMock(return_value="invalid_token")
        mock_authenticator.get_claims = AsyncMock(side_effect=ValueError("Failed to decode access token"))
        target = mock_copilot_target

        with pytest.raises(ValueError, match="Failed to decode access token"):
            await target._build_websocket_url_async(session_id="test", copilot_conversation_id="test")


@pytest.mark.usefixtures("patch_central_database")
class TestUploadImageAsync:
    @pytest.mark.asyncio
    async def test_upload_image_async_successful(self, mock_authenticator, mock_copilot_target):
        target = mock_copilot_target
        image_path = "/path/to/image.png"
        data_url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA"
        conversation_id = "conv_123"
        expected_doc_id = "doc_abc123"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"docId": expected_doc_id}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = make_mock_async_client(mock_response)
            mock_client_class.return_value = mock_client

            doc_id = await target._upload_image_async(
                image_path=image_path, data_url=data_url, conversation_id=conversation_id
            )

            assert doc_id == expected_doc_id
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args.args[0] == "https://substrate.office.com/m365Copilot/UploadFile"
            assert "Authorization" in call_args.kwargs["headers"]
            assert call_args.kwargs["data"]["conversationId"] == conversation_id
            assert call_args.kwargs["data"]["FileBase64"] == data_url

    @pytest.mark.asyncio
    async def test_upload_image_async_non_200_status(self, mock_authenticator, mock_copilot_target):
        target = mock_copilot_target

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client_class.return_value = make_mock_async_client(mock_response)

            with pytest.raises(RuntimeError, match="Failed to upload image. Status: 500"):
                await target._upload_image_async(
                    image_path="/path/to/image.png", data_url="data:image/png;base64,abc", conversation_id="conv_123"
                )

    @pytest.mark.asyncio
    async def test_upload_image_async_missing_doc_id(self, mock_authenticator, mock_copilot_target):
        target = mock_copilot_target

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"someOtherField": "value"}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client_class.return_value = make_mock_async_client(mock_response)

            with pytest.raises(RuntimeError, match="No docId in upload response"):
                await target._upload_image_async(
                    image_path="/path/to/image.png", data_url="data:image/png;base64,abc", conversation_id="conv_123"
                )

    @pytest.mark.asyncio
    async def test_upload_image_async_uses_correct_headers(self, mock_authenticator, mock_copilot_target):
        target = mock_copilot_target
        expected_token = await mock_authenticator.get_token_async()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"docId": "test_doc_id"}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = make_mock_async_client(mock_response)
            mock_client_class.return_value = mock_client

            await target._upload_image_async(
                image_path="/path/to/image.png", data_url="data:image/png;base64,abc", conversation_id="conv_123"
            )

            call_args = mock_client.post.call_args
            headers = call_args.kwargs["headers"]
            assert headers["Authorization"] == f"Bearer {expected_token}"
            assert headers["X-Scenario"] == "OfficeWebIncludedCopilot"
            assert headers["X-Variants"] == "feature.EnableImageSupportInUploadFile"
            assert headers["Origin"] == "https://m365.cloud.microsoft"
            assert headers["Referer"] == "https://m365.cloud.microsoft/"


@pytest.mark.usefixtures("patch_central_database")
class TestProcessImagePieceAsync:
    @pytest.mark.asyncio
    async def test_process_image_piece_async_successful(
        self, mock_authenticator, mock_copilot_target, patch_convert_local_image_to_data_url
    ):
        target = mock_copilot_target
        image_path = "/path/to/image.png"
        copilot_conversation_id = "conv_123"
        expected_doc_id = "doc_xyz789"

        with patch.object(target, "_upload_image_async", new=AsyncMock(return_value=expected_doc_id)):
            annotation = await target._process_image_piece_async(
                image_path=image_path,
                copilot_conversation_id=copilot_conversation_id,
            )
            assert annotation["id"] == expected_doc_id
            assert annotation["messageAnnotationType"] == "ImageFile"
            assert annotation["messageAnnotationMetadata"]["@type"] == "File"
            assert annotation["messageAnnotationMetadata"]["annotationType"] == "File"
            assert annotation["messageAnnotationMetadata"]["fileName"] == "image.png"
            assert annotation["messageAnnotationMetadata"]["fileType"] == "png"

    @pytest.mark.asyncio
    async def test_process_image_piece_async_extracts_filename_correctly(
        self, mock_authenticator, mock_copilot_target, patch_convert_local_image_to_data_url
    ):
        target = mock_copilot_target
        test_cases = [
            ("/path/to/image.png", "image.png", "png"),
            ("C:\\Users\\test\\photo.jpg", "photo.jpg", "jpg"),
            ("simple_image.gif", "simple_image.gif", "gif"),
            ("/path/with.dots/image.png", "image.png", "png"),
            ("no_extension", "no_extension", "png"),  # defaults to png
        ]

        for image_path, expected_name, expected_type in test_cases:
            with patch.object(target, "_upload_image_async", new=AsyncMock(return_value="doc_id")):
                annotation = await target._process_image_piece_async(
                    image_path=image_path,
                    copilot_conversation_id="conv_123",
                )
                assert annotation["messageAnnotationMetadata"]["fileName"] == expected_name
                assert annotation["messageAnnotationMetadata"]["fileType"] == expected_type

    @pytest.mark.asyncio
    async def test_process_image_piece_async_propagates_upload_error(
        self, mock_authenticator, mock_copilot_target, patch_convert_local_image_to_data_url
    ):
        target = mock_copilot_target
        with patch.object(target, "_upload_image_async", new=AsyncMock(side_effect=RuntimeError("Upload failed"))):
            with pytest.raises(RuntimeError, match="Upload failed"):
                await target._process_image_piece_async(
                    image_path="/path/to/image.png",
                    copilot_conversation_id="conv_123",
                )


@pytest.mark.usefixtures("patch_central_database")
class TestBuildPromptMessage:
    @pytest.mark.asyncio
    async def test_build_prompt_message_structure(self, mock_authenticator, sample_text_pieces, mock_copilot_target):
        target = mock_copilot_target

        message = await target._build_prompt_message(
            message_pieces=sample_text_pieces,
            session_id="session_123",
            copilot_conversation_id="conv_456",
            is_start_of_session=True,
        )

        assert message["target"] == "chat"
        assert message["type"] == CopilotMessageType.USER_PROMPT
        assert message["invocationId"] == "0"

        args = message["arguments"][0]
        assert args["sessionId"] == "session_123"
        assert args["conversationId"] == "conv_456"
        assert args["isStartOfSession"] is True
        assert args["source"] == "officeweb"
        assert args["productThreadType"] == "Office"

        msg = args["message"]
        assert msg["text"] == "Hello"
        assert msg["author"] == "user"
        assert msg["messageType"] == "Chat"
        assert msg["locale"] == "en-us"

    @pytest.mark.asyncio
    async def test_build_prompt_message_with_different_session_states(
        self, mock_authenticator, sample_text_pieces, mock_copilot_target
    ):
        target = mock_copilot_target

        message = await target._build_prompt_message(
            message_pieces=sample_text_pieces,
            session_id="session_123",
            copilot_conversation_id="conv_456",
            is_start_of_session=False,
        )

        assert message["arguments"][0]["isStartOfSession"] is False

    @pytest.mark.asyncio
    async def test_build_prompt_message_with_image(
        self,
        mock_authenticator,
        sample_image_pieces,
        mock_copilot_target,
        patch_convert_local_image_to_data_url,
        make_annotation,
    ):
        target = mock_copilot_target
        expected_annotation = make_annotation(doc_id="doc_id_xyz", file_name="image.png")

        with patch.object(
            target, "_process_image_piece_async", new=AsyncMock(return_value=expected_annotation)
        ) as mock_process:
            message = await target._build_prompt_message(
                message_pieces=sample_image_pieces,
                session_id="session_123",
                copilot_conversation_id="conv_456",
                is_start_of_session=True,
            )

            args = message["arguments"][0]
            msg = args["message"]

            # Text should be empty when only images are sent
            assert msg["text"] == ""
            assert "messageAnnotations" in msg
            assert len(msg["messageAnnotations"]) == 1

            annotation = msg["messageAnnotations"][0]
            assert annotation["id"] == "doc_id_xyz"
            assert annotation["messageAnnotationType"] == "ImageFile"

            mock_process.assert_called_once_with(
                image_path="/path/to/image.png",
                copilot_conversation_id="conv_456",
            )
            assert annotation["messageAnnotationMetadata"]["@type"] == "File"
            assert annotation["messageAnnotationMetadata"]["annotationType"] == "File"
            assert annotation["messageAnnotationMetadata"]["fileType"] == "png"
            assert annotation["messageAnnotationMetadata"]["fileName"] == "image.png"

    @pytest.mark.asyncio
    async def test_build_prompt_message_with_mixed_content(
        self,
        mock_authenticator,
        sample_mixed_pieces,
        mock_copilot_target,
        patch_convert_local_image_to_data_url,
        make_annotation,
    ):
        target = mock_copilot_target
        mock_annotations = [
            make_annotation(doc_id="doc_id_1", file_name="image1.png"),
            make_annotation(doc_id="doc_id_2", file_name="image2.jpg"),
        ]

        with patch.object(
            target, "_process_image_piece_async", new=AsyncMock(side_effect=mock_annotations)
        ) as mock_process:
            message = await target._build_prompt_message(
                message_pieces=sample_mixed_pieces,
                session_id="session_123",
                copilot_conversation_id="conv_456",
                is_start_of_session=True,
            )

            args = message["arguments"][0]
            msg = args["message"]

            assert msg["text"] == "Describe this image"

            # Should have message annotations for both images
            assert "messageAnnotations" in msg
            assert len(msg["messageAnnotations"]) == 2

            annotation1 = msg["messageAnnotations"][0]
            assert annotation1["id"] == "doc_id_1"
            assert annotation1["messageAnnotationMetadata"]["fileName"] == "image1.png"
            assert annotation1["messageAnnotationMetadata"]["fileType"] == "png"

            annotation2 = msg["messageAnnotations"][1]
            assert annotation2["id"] == "doc_id_2"
            assert annotation2["messageAnnotationMetadata"]["fileName"] == "image2.jpg"
            assert annotation2["messageAnnotationMetadata"]["fileType"] == "jpg"

            assert mock_process.call_count == 2

    @pytest.mark.asyncio
    async def test_build_prompt_message_with_multiple_text_pieces(
        self, mock_authenticator, mock_copilot_target, make_message_piece
    ):
        target = mock_copilot_target
        text_pieces = [make_message_piece("First line"), make_message_piece("Second line")]

        message = await target._build_prompt_message(
            message_pieces=text_pieces,
            session_id="session_123",
            copilot_conversation_id="conv_456",
            is_start_of_session=True,
        )

        msg = message["arguments"][0]["message"]

        # Multiple text pieces should be joined with newlines
        assert msg["text"] == "First line\nSecond line"
        assert "messageAnnotations" not in msg


@pytest.mark.usefixtures("patch_central_database")
class TestConnectAndSend:
    @pytest.mark.asyncio
    async def test_connect_and_send_successful_response(self, mock_authenticator, sample_text_pieces, mock_websocket):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator)
        mock_websocket.recv = AsyncMock(
            side_effect=[
                '{"type":6}\x1e',  # PING response to handshake
                '{"type":1}\x1e',  # partial response to user prompt
                '{"type":2,"item":{"result":{"message":"Hello from Copilot"}}}\x1e',  # final content
            ]
        )

        with patch("websockets.connect", return_value=mock_websocket):
            response = await target._connect_and_send(
                message_pieces=sample_text_pieces,
                session_id="session_123",
                copilot_conversation_id="conv_456",
                is_start_of_session=True,
            )

        assert response == "Hello from Copilot"
        assert mock_websocket.send.call_count == 2  # handshake + user prompt

    @pytest.mark.asyncio
    async def test_connect_and_send_timeout(self, mock_authenticator, sample_text_pieces, mock_websocket):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator, response_timeout_seconds=1)
        mock_websocket.recv = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch("websockets.connect", return_value=mock_websocket):
            with pytest.raises(TimeoutError, match="Timed out waiting for Copilot response"):
                await target._connect_and_send(
                    message_pieces=sample_text_pieces,
                    session_id="session_123",
                    copilot_conversation_id="conv_456",
                    is_start_of_session=True,
                )

    @pytest.mark.asyncio
    async def test_connect_and_send_none_response(self, mock_authenticator, sample_text_pieces, mock_websocket):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator)
        mock_websocket.recv = AsyncMock(return_value=None)

        with patch("websockets.connect", return_value=mock_websocket):
            with pytest.raises(RuntimeError, match="WebSocket connection closed unexpectedly"):
                await target._connect_and_send(
                    message_pieces=sample_text_pieces,
                    session_id="session_123",
                    copilot_conversation_id="conv_456",
                    is_start_of_session=True,
                )

    @pytest.mark.asyncio
    async def test_connect_and_send_stream_end_without_final_content(
        self, mock_authenticator, sample_text_pieces, mock_websocket
    ):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator)
        mock_websocket.recv = AsyncMock(side_effect=['{"type":6}\x1e', '{"type":3}\x1e'])

        with patch("websockets.connect", return_value=mock_websocket):
            response = await target._connect_and_send(
                message_pieces=sample_text_pieces,
                session_id="sid",
                copilot_conversation_id="cid",
                is_start_of_session=True,
            )

        assert response == ""

    @pytest.mark.asyncio
    async def test_connect_and_send_exceeds_max_iterations(
        self, mock_authenticator, sample_text_pieces, mock_websocket
    ):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator)
        mock_websocket.recv = AsyncMock(return_value='{"type":1}\x1e')

        with patch("websockets.connect", return_value=mock_websocket):
            with pytest.raises(RuntimeError, match="Exceeded maximum message iterations"):
                await target._connect_and_send(
                    message_pieces=sample_text_pieces,
                    session_id="sid",
                    copilot_conversation_id="cid",
                    is_start_of_session=True,
                )

    @pytest.mark.asyncio
    async def test_connect_and_send_with_image_pieces(self, mock_authenticator, sample_image_pieces, mock_websocket):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator)

        mock_websocket.recv = AsyncMock(
            side_effect=[
                '{"type":6}\x1e',  # PING
                '{"type":2,"item":{"result":{"message":"Image analyzed"}}}\x1e',
            ]
        )

        with patch("websockets.connect", return_value=mock_websocket):
            with patch.object(target, "_upload_image_async", new=AsyncMock(return_value="doc_id_123")):
                with patch(
                    "pyrit.prompt_target.websocket_copilot_target.convert_local_image_to_data_url",
                    new=AsyncMock(return_value="data:image/png;base64,abc123"),
                ):
                    response = await target._connect_and_send(
                        message_pieces=sample_image_pieces,
                        session_id="sid",
                        copilot_conversation_id="cid",
                        is_start_of_session=True,
                    )

                assert response == "Image analyzed"
                assert mock_websocket.send.call_count == 2

    @pytest.mark.asyncio
    async def test_connect_and_send_with_mixed_content(self, mock_authenticator, sample_mixed_pieces, mock_websocket):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator)
        mock_websocket.recv = AsyncMock(
            side_effect=[
                '{"type":6}\x1e',
                '{"type":2,"item":{"result":{"message":"Mixed content processed"}}}\x1e',
            ]
        )

        with patch("websockets.connect", return_value=mock_websocket):
            with patch.object(target, "_upload_image_async", new=AsyncMock(side_effect=["doc_id_1", "doc_id_2"])):
                with patch(
                    "pyrit.prompt_target.websocket_copilot_target.convert_local_image_to_data_url",
                    new=AsyncMock(return_value="data:image/png;base64,abc123"),
                ):
                    response = await target._connect_and_send(
                        message_pieces=sample_mixed_pieces,
                        session_id="sid",
                        copilot_conversation_id="cid",
                        is_start_of_session=True,
                    )

                assert response == "Mixed content processed"


@pytest.mark.usefixtures("patch_central_database")
class TestValidateRequest:
    @pytest.mark.parametrize(
        "data_type,value,should_pass",
        [
            ("text", "Example text", True),
            ("image_path", "/path/to/image.png", True),
            ("audio_path", "/path/to/audio.mp3", False),
            ("video_path", "/path/to/video.mp4", False),
        ],
    )
    def test_validate_request_data_types(self, mock_authenticator, make_message_piece, data_type, value, should_pass):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator)
        message_piece = make_message_piece(value, data_type=data_type, conversation_id="123")
        message = Message(message_pieces=[message_piece])

        if should_pass:
            target._validate_request(normalized_conversation=[message])
        else:
            with pytest.raises(
                ValueError,
                match=f"This target supports only the following data types: image_path, text. Received: {data_type}.",
            ):
                target._validate_request(normalized_conversation=[message])

    def test_validate_request_with_multiple_text_pieces(self, mock_authenticator, make_message_piece):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator)
        message_pieces = [make_message_piece(f"test{i}", conversation_id="123") for i in range(3)]
        message = Message(message_pieces=message_pieces)
        target._validate_request(normalized_conversation=[message])  # should not raise

    def test_validate_request_with_mixed_valid_content(self, mock_authenticator, make_message_piece):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator)
        message_pieces = [
            make_message_piece("text content", conversation_id="123"),
            make_message_piece("/path/to/image.png", data_type="image_path", conversation_id="123"),
        ]
        message = Message(message_pieces=message_pieces)
        target._validate_request(normalized_conversation=[message])  # should not raise


@pytest.mark.usefixtures("patch_central_database")
class TestIsStartOfSession:
    def test_is_start_of_session_with_empty_history(self, mock_authenticator):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator)

        mock_memory = MagicMock()
        mock_memory.get_conversation.return_value = []
        target._memory = mock_memory

        conversation_id = "test_conv_123"
        result = target._is_start_of_session(conversation_id=conversation_id)

        assert result is True
        mock_memory.get_conversation.assert_called_once_with(conversation_id=conversation_id)

    def test_is_start_of_session_with_existing_history(self, mock_authenticator):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator)

        mock_memory = MagicMock()
        mock_message = MagicMock()
        mock_memory.get_conversation.return_value = [mock_message]
        target._memory = mock_memory

        conversation_id = "test_conv_123"
        result = target._is_start_of_session(conversation_id=conversation_id)

        assert result is False


@pytest.mark.usefixtures("patch_central_database")
class TestGenerateConsistentCopilotIds:
    def test_generates_consistent_ids_for_same_conversation(self, mock_authenticator):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator)

        pyrit_conv_id = "pyrit_conversation_123"

        session_id_1, copilot_conv_id_1 = target._generate_consistent_copilot_ids(pyrit_conversation_id=pyrit_conv_id)
        session_id_2, copilot_conv_id_2 = target._generate_consistent_copilot_ids(pyrit_conversation_id=pyrit_conv_id)

        assert session_id_1 == session_id_2
        assert copilot_conv_id_1 == copilot_conv_id_2

    def test_generates_different_ids_for_different_conversations(self, mock_authenticator):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator)

        pyrit_conv_id_1 = "pyrit_conversation_123"
        pyrit_conv_id_2 = "pyrit_conversation_456"

        session_id_1, copilot_conv_id_1 = target._generate_consistent_copilot_ids(pyrit_conversation_id=pyrit_conv_id_1)
        session_id_2, copilot_conv_id_2 = target._generate_consistent_copilot_ids(pyrit_conversation_id=pyrit_conv_id_2)

        assert session_id_1 != session_id_2
        assert copilot_conv_id_1 != copilot_conv_id_2

    def test_generated_ids_are_valid_uuids(self, mock_authenticator):
        """Test that generated IDs are valid UUID format and can be parsed."""
        import uuid

        target = WebSocketCopilotTarget(authenticator=mock_authenticator)

        pyrit_conv_id = "test_conversation"
        session_id, copilot_conv_id = target._generate_consistent_copilot_ids(pyrit_conversation_id=pyrit_conv_id)

        # Should be parseable as UUIDs without raising exceptions
        uuid.UUID(session_id)
        uuid.UUID(copilot_conv_id)


@pytest.mark.usefixtures("patch_central_database")
class TestSendPromptAsync:
    @pytest.mark.asyncio
    async def test_send_prompt_async_successful(self, mock_authenticator, make_message_piece, mock_memory):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator)
        target._memory = mock_memory
        message = Message(message_pieces=[make_message_piece("Hello", conversation_id="conv_123")])

        with patch.object(target, "_connect_and_send", new=AsyncMock(return_value="Response from Copilot")):
            responses = await target.send_prompt_async(message=message)

        assert len(responses) == 1
        assert responses[0].message_pieces[0].converted_value == "Response from Copilot"
        assert responses[0].message_pieces[0].api_role == "assistant"

    @pytest.mark.asyncio
    async def test_send_prompt_async_with_exceptions(self, mock_authenticator, make_message_piece, mock_memory):
        from pyrit.exceptions import EmptyResponseException

        target = WebSocketCopilotTarget(authenticator=mock_authenticator)
        target._memory = mock_memory
        message = Message(message_pieces=[make_message_piece("Hello", conversation_id="conv_123")])

        # Test for various empty responses
        for response in [None, "", "   \n\t  "]:
            with patch.object(target, "_connect_and_send", new=AsyncMock(return_value=response)):
                with pytest.raises(EmptyResponseException, match="Copilot returned an empty response"):
                    await target.send_prompt_async(message=message)

        # Test for generic exception during WebSocket communication
        with patch.object(target, "_connect_and_send", new=AsyncMock(side_effect=Exception("Test error"))):
            with pytest.raises(RuntimeError, match="An error occurred during WebSocket communication"):
                await target.send_prompt_async(message=message)

    @pytest.mark.asyncio
    async def test_send_prompt_async_with_image(self, mock_authenticator, make_message_piece, mock_memory):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator)
        target._memory = mock_memory
        message = Message(
            message_pieces=[
                make_message_piece("/path/to/image.png", data_type="image_path", conversation_id="conv_123")
            ]
        )

        with patch.object(target, "_connect_and_send", new=AsyncMock(return_value="Image description response")):
            responses = await target.send_prompt_async(message=message)

            assert len(responses) == 1
            assert responses[0].message_pieces[0].converted_value == "Image description response"
            assert responses[0].message_pieces[0].api_role == "assistant"

    @pytest.mark.asyncio
    async def test_send_prompt_async_with_mixed_content(self, mock_authenticator, make_message_piece, mock_memory):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator)
        target._memory = mock_memory
        message_pieces = [
            make_message_piece("What's in this image?", conversation_id="conv_123"),
            make_message_piece("/path/to/image.png", data_type="image_path", conversation_id="conv_123"),
        ]
        message = Message(message_pieces=message_pieces)

        with patch.object(
            target, "_connect_and_send", new=AsyncMock(return_value="This image shows a beautiful landscape")
        ):
            responses = await target.send_prompt_async(message=message)

            assert len(responses) == 1
            assert responses[0].message_pieces[0].converted_value == "This image shows a beautiful landscape"

    @pytest.mark.asyncio
    async def test_send_prompt_async_calls_validation(self, mock_authenticator, make_message_piece, mock_memory):
        target = WebSocketCopilotTarget(authenticator=mock_authenticator)
        target._memory = mock_memory
        message = Message(
            message_pieces=[
                make_message_piece("/path/to/audio.mp3", data_type="audio_path", conversation_id="conv_123")
            ]
        )

        with pytest.raises(
            ValueError,
            match="This target supports only the following data types.*If your target does support this, set the"
            " custom_configuration parameter accordingly",
        ):
            await target.send_prompt_async(message=message)
