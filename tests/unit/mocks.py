# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import shutil
import tempfile
import uuid
from collections.abc import Generator, MutableSequence, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any
from unittest.mock import MagicMock, patch

from pyrit.memory import AzureSQLMemory, CentralMemory, PromptMemoryEntry
from pyrit.models import (
    ComponentIdentifier,
    Message,
    MessagePiece,
    ScenarioIdentifier,
    ScenarioResult,
    flatten_to_message_pieces,
)
from pyrit.prompt_target import PromptTarget, TargetCapabilities, TargetConfiguration, limit_requests_per_minute


def make_scenario_identifier(
    *,
    scenario_name: str = "TestScenario",
    scenario_module: str = "tests.unit.mocks",
    version: int = 1,
    objective_target: ComponentIdentifier | None = None,
    objective_scorer: ComponentIdentifier | None = None,
    techniques: list[str] | None = None,
    datasets: list[str] | None = None,
    params: dict[str, Any] | None = None,
    pyrit_version: str | None = None,
) -> ScenarioIdentifier:
    """
    Build a ``ScenarioIdentifier`` for tests.

    Mirrors what ``Scenario._build_scenario_identifier`` produces so tests can
    construct a ``ScenarioResult`` without a live scenario.
    """
    extra: dict[str, Any] = {}
    if pyrit_version is not None:
        extra["pyrit_version"] = pyrit_version
    return ScenarioIdentifier(
        class_name=scenario_name,
        class_module=scenario_module,
        version=version,
        techniques=techniques,
        datasets=datasets,
        params=dict(params) if params else {},
        objective_target=objective_target,
        objective_scorer=objective_scorer,
        **extra,
    )


def make_scenario_result(
    *,
    scenario_name: str = "TestScenario",
    scenario_version: int = 1,
    objective_target_identifier: ComponentIdentifier | None = None,
    objective_scorer_identifier: ComponentIdentifier | None = None,
    techniques: list[str] | None = None,
    datasets: list[str] | None = None,
    params: dict[str, Any] | None = None,
    pyrit_version: str | None = None,
    **kwargs: Any,
) -> ScenarioResult:
    """
    Build a ``ScenarioResult`` for tests from flat identity kwargs.

    The identity kwargs (``scenario_name`` / ``scenario_version`` /
    ``objective_target_identifier`` / ``objective_scorer_identifier`` /
    ``techniques`` / ``datasets`` / ``params`` / ``pyrit_version``) are folded
    into a ``ScenarioIdentifier``; all other kwargs pass through to
    ``ScenarioResult``.
    """
    identifier = make_scenario_identifier(
        scenario_name=scenario_name,
        version=scenario_version,
        objective_target=objective_target_identifier,
        objective_scorer=objective_scorer_identifier,
        techniques=techniques,
        datasets=datasets,
        params=params,
        pyrit_version=pyrit_version,
    )
    return ScenarioResult(scenario_identifier=identifier, **kwargs)


def get_mock_scorer_identifier() -> ComponentIdentifier:
    """
    Returns a mock ComponentIdentifier for use in tests where the specific
    scorer identity doesn't matter.
    """
    return ComponentIdentifier(
        class_name="MockScorer",
        class_module="tests.unit.mocks",
    )


def get_mock_target_identifier(name: str = "MockTarget", module: str = "tests.unit.mocks") -> ComponentIdentifier:
    """
    Returns a mock ComponentIdentifier for use in tests where the specific
    target identity doesn't matter.

    Args:
        name: The class name for the mock target. Defaults to "MockTarget".
        module: The module path for the mock target. Defaults to "tests.unit.mocks".

    Returns:
        A ComponentIdentifier configured with the provided name and module.
    """
    return ComponentIdentifier(
        class_name=name,
        class_module=module,
    )


def get_mock_attack_identifier(name: str = "MockAttack", module: str = "tests.unit.mocks") -> ComponentIdentifier:
    """
    Returns a mock ComponentIdentifier for use in tests where the specific
    attack identity doesn't matter.

    Args:
        name: The class name for the mock attack. Defaults to "MockAttack".
        module: The module path for the mock attack. Defaults to "tests.unit.mocks".

    Returns:
        A ComponentIdentifier configured with the provided name and module.
    """
    return ComponentIdentifier(
        class_name=name,
        class_module=module,
    )


def get_mock_target(name: str = "MockTarget") -> MagicMock:
    """
    Returns a MagicMock target whose ``get_identifier()`` returns a real
    ``ComponentIdentifier``. Use this wherever a ``MagicMock(spec=PromptTarget)``
    is needed as an ``objective_target``.

    Args:
        name: The class name for the mock target. Defaults to "MockTarget".

    Returns:
        A MagicMock configured to return a real ComponentIdentifier.
    """
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = get_mock_target_identifier(name)
    return target


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


class MockPromptTarget(PromptTarget):
    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_multi_message_pieces=True,
            supports_system_prompt=True,
            supports_editable_history=True,
        )
    )

    prompt_sent: list[str]

    def __init__(self, *, id=None, rpm=None) -> None:  # noqa: A002
        super().__init__(max_requests_per_minute=rpm)
        self.id = id
        self.prompt_sent = []

    def set_system_prompt(
        self,
        *,
        system_prompt: str,
        conversation_id: str,
        attack_identifier: ComponentIdentifier | None = None,
        labels: dict[str, str] | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        if self._memory:
            self._memory.add_message_to_memory(
                request=MessagePiece(
                    role="system",
                    original_value=system_prompt,
                    converted_value=system_prompt,
                    conversation_id=conversation_id,
                ).to_message()
            )

    @limit_requests_per_minute
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        message = normalized_conversation[-1]
        self.prompt_sent.append(message.get_value())

        return [
            MessagePiece(
                role="assistant",
                original_value="default",
                conversation_id=message.message_pieces[0].conversation_id,
            ).to_message()
        ]

    def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
        """
        Validates the provided message
        """


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

        return [
            MessagePiece(
                role="user",
                original_value="original prompt text",
                converted_value="Hello, how are you?",
                conversation_id=conversation_1,
                sequence=0,
            ).to_message(),
            MessagePiece(
                role="assistant",
                original_value="original prompt text",
                converted_value="I'm fine, thank you!",
                conversation_id=conversation_1,
                sequence=1,
            ).to_message(),
            MessagePiece(
                role="assistant",
                original_value="original prompt text",
                converted_value="I'm fine, thank you!",
                conversation_id=str(uuid.uuid4()),
            ).to_message(),
        ]


def get_sample_conversation_entries() -> Sequence[PromptMemoryEntry]:
    conversations = get_sample_conversations()
    pieces = flatten_to_message_pieces(conversations)
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
