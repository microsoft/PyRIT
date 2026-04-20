# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import shutil
import tempfile
import uuid
from contextlib import AbstractAsyncContextManager
from typing import Generator, MutableSequence, Optional, Sequence
from unittest.mock import MagicMock, patch

from pyrit.identifiers import ScorerIdentifier
from pyrit.memory import AzureSQLMemory, CentralMemory, PromptMemoryEntry
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import PromptChatTarget, limit_requests_per_minute


def get_mock_scorer_identifier() -> ScorerIdentifier:
    """
    Returns a mock ScorerIdentifier for use in tests where the specific
    scorer identity doesn't matter.
    """
    return ScorerIdentifier(
        class_name="MockScorer",
        class_module="tests.unit.mocks",
        class_description="Mock scorer for testing",
        identifier_type="instance",
    )


class MockHttpPostAsync(AbstractAsyncContextManager):
    def __init__(self, url, headers=None, json=None, params=None, ssl=None):
        self.status = 200
        if url == "http://aml-test-endpoint.com":
            self._json = [{"0": "extracted response"}]
        else:
            raise NotImplementedError(f"No mock for HTTP POST {url}")

    async def json(self, content_type="application/json"):
        return self._json

    async def raise_for_status(self):
        if not (200 <= self.status < 300):
            raise Exception(f"HTTP Error {self.status}")

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def __aenter__(self):
        return self


class MockHttpPostSync:
    def __init__(self, url, headers=None, json=None, params=None, ssl=None):
        self.status = 200
        self.status_code = 200
        if url == "http://aml-test-endpoint.com":
            self._json = [{"0": "extracted response"}]
        else:
            raise NotImplementedError(f"No mock for HTTP POST {url}")

    def json(self, content_type="application/json"):
        return self._json

    def raise_for_status(self):
        if not (200 <= self.status < 300):
            raise Exception(f"HTTP Error {self.status}")


class MockPromptTarget(PromptChatTarget):
    prompt_sent: list[str]

    def __init__(self, id=None, rpm=None) -> None:
        super().__init__(max_requests_per_minute=rpm)
        self.id = id
        self.prompt_sent = []

    def set_system_prompt(
        self,
        *,
        system_prompt: str,
        conversation_id: str,
        attack_identifier: Optional[dict[str, str]] = None,
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        self.system_prompt = system_prompt
        if self._memory:
            self._memory.add_message_to_memory(
                request=MessagePiece(
                    role="system",
                    original_value=system_prompt,
                    converted_value=system_prompt,
                    conversation_id=conversation_id,
                    attack_identifier=attack_identifier,
                    labels=labels,
                ).to_message()
            )

    @limit_requests_per_minute
    async def send_prompt_async(self, *, message: Message) -> list[Message]:
        self.prompt_sent.append(message.get_value())

        return [
            MessagePiece(
                role="assistant",
                original_value="default",
                conversation_id=message.message_pieces[0].conversation_id,
                attack_identifier=message.message_pieces[0].attack_identifier,
                labels=message.message_pieces[0].labels,
            ).to_message()
        ]

    def _validate_request(self, *, message: Message) -> None:
        """
        Validates the provided message
        """

    def is_json_response_supported(self) -> bool:
        return False


def get_azure_sql_memory() -> Generator[AzureSQLMemory, None, None]:
    # Create a test Azure SQL Server DB using in-memory SQLite
    # This allows testing actual SQL queries (including JOINs and metadata filtering)
    # without requiring a real Azure SQL instance
    with (
        patch("pyrit.memory.AzureSQLMemory._create_auth_token") as create_auth_token_mock,
        patch("pyrit.memory.AzureSQLMemory._enable_azure_authorization") as enable_azure_authorization_mock,
    ):
        os.environ[AzureSQLMemory.AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL] = (
            "https://test.blob.core.windows.net/test"
        )
        os.environ[AzureSQLMemory.AZURE_STORAGE_ACCOUNT_DB_DATA_SAS_TOKEN] = "valid_sas_token"

        # Use in-memory SQLite instead of mock to allow real SQL queries
        azure_sql_memory = AzureSQLMemory(
            connection_string="sqlite:///:memory:",
            results_container_url=os.environ[AzureSQLMemory.AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL],
            results_sas_token=os.environ[AzureSQLMemory.AZURE_STORAGE_ACCOUNT_DB_DATA_SAS_TOKEN],
        )

        create_auth_token_mock.return_value = "token"
        enable_azure_authorization_mock.return_value = None

        # Create a temporary directory for results
        temp_dir = tempfile.mkdtemp()
        azure_sql_memory.results_path = temp_dir

        azure_sql_memory.disable_embedding()

        # Initialize the database schema
        azure_sql_memory.reset_database()

        CentralMemory.set_memory_instance(azure_sql_memory)
        yield azure_sql_memory

    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    azure_sql_memory.dispose_engine()


def get_image_message_piece() -> MessagePiece:
    file_name: str
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
        file_name = temp_file.name
        temp_file.write(b"image data")

        return MessagePiece(
            role="user",
            original_value=file_name,
            converted_value=file_name,
            original_value_data_type="image_path",
            converted_value_data_type="image_path",
        )


def get_audio_message_piece() -> MessagePiece:
    file_name: str
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
        file_name = temp_file.name
        temp_file.write(b"audio data")

        return MessagePiece(
            role="user",
            original_value=file_name,
            converted_value=file_name,
            original_value_data_type="audio_path",
            converted_value_data_type="audio_path",
        )


def get_test_message_piece() -> MessagePiece:
    return MessagePiece(
        role="user",
        original_value="some text",
        converted_value="some text",
        original_value_data_type="text",
        converted_value_data_type="text",
    )


def get_sample_conversations() -> MutableSequence[Message]:
    with patch.object(CentralMemory, "get_memory_instance", return_value=MagicMock()):
        conversation_1 = str(uuid.uuid4())
        attack_identifier = {
            "__type__": "MockPromptTarget",
            "__module__": "unit.mocks",
            "id": str(uuid.uuid4()),
        }

        return [
            MessagePiece(
                role="user",
                original_value="original prompt text",
                converted_value="Hello, how are you?",
                conversation_id=conversation_1,
                sequence=0,
                attack_identifier=attack_identifier,
            ).to_message(),
            MessagePiece(
                role="assistant",
                original_value="original prompt text",
                converted_value="I'm fine, thank you!",
                conversation_id=conversation_1,
                sequence=1,
                attack_identifier=attack_identifier,
            ).to_message(),
            MessagePiece(
                role="assistant",
                original_value="original prompt text",
                converted_value="I'm fine, thank you!",
                conversation_id=str(uuid.uuid4()),
                attack_identifier=attack_identifier,
            ).to_message(),
        ]


def get_sample_conversation_entries() -> Sequence[PromptMemoryEntry]:
    conversations = get_sample_conversations()
    pieces = Message.flatten_to_message_pieces(conversations)
    return [PromptMemoryEntry(entry=piece) for piece in pieces]


def openai_chat_response_json_dict() -> dict:
    return {
        "id": "12345678-1a2b-3c4e5f-a123-12345678abcd",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hi"},
                "finish_reason": "stop",
            }
        ],
        "model": "o4-mini",
    }


def openai_response_json_dict() -> dict:
    return {
        "id": "resp_12345678-1a2b-3c4e5f-a123-12345678abcd",
        "object": "response",
        "status": "completed",
        "error": None,
        "output": [
            {
                "id": "msg_12428471298473947293847293847",
                "role": "assistant",
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "hi"},
                ],
            }
        ],
        "model": "o4-mini",
    }


def openai_failed_response_json_dict() -> dict:
    return {
        "id": "resp_12345678-1a2b-3c4e5f-a123-12345678abcd",
        "object": "response",
        "status": "failed",
        "error": {"code": "invalid_request", "message": "Invalid request"},
        "model": "o4-mini",
    }
