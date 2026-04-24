# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Generator
from typing import Optional

from sqlalchemy import inspect

from pyrit.identifiers import ComponentIdentifier
from pyrit.memory import MemoryInterface, SQLiteMemory
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import PromptChatTarget, limit_requests_per_minute


def get_memory_interface() -> Generator[MemoryInterface, None, None]:
    yield from get_sqlite_memory()


def get_sqlite_memory() -> Generator[SQLiteMemory, None, None]:
    # Create an in-memory SQLite engine
    sqlite_memory = SQLiteMemory(db_path=":memory:")

    sqlite_memory.disable_embedding()

    # Reset the database to ensure a clean state
    sqlite_memory.reset_database()
    inspector = inspect(sqlite_memory.engine)

    # Verify that tables are created as expected
    assert "PromptMemoryEntries" in inspector.get_table_names(), "PromptMemoryEntries table not created."
    assert "EmbeddingData" in inspector.get_table_names(), "EmbeddingData table not created."
    assert "ScoreEntries" in inspector.get_table_names(), "ScoreEntries table not created."
    assert "SeedPromptEntries" in inspector.get_table_names(), "SeedPromptEntries table not created."

    yield sqlite_memory
    sqlite_memory.dispose_engine()


class MockPromptTarget(PromptChatTarget):
    prompt_sent: list[str]

    def __init__(self, id=None, rpm=None) -> None:  # noqa: A002
        super().__init__(max_requests_per_minute=rpm)
        self.id = id  # noqa: A003
        self.prompt_sent = []

    def set_system_prompt(
        self,
        *,
        system_prompt: str,
        conversation_id: str,
        attack_identifier: Optional[ComponentIdentifier] = None,
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
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        message = normalized_conversation[-1]
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

    def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
        """
        Validates the provided message
        """
