# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import AsyncGenerator
from contextlib import aclosing

from sqlalchemy import inspect

from pyrit.memory import MemoryInterface, SQLiteMemory
from pyrit.models import ComponentIdentifier, Message, MessagePiece
from pyrit.prompt_target import PromptTarget, TargetCapabilities, TargetConfiguration, limit_requests_per_minute


async def get_memory_interface_async() -> AsyncGenerator[MemoryInterface, None]:
    async with aclosing(get_sqlite_memory_async()) as memories:
        async for memory in memories:
            yield memory


async def get_sqlite_memory_async() -> AsyncGenerator[SQLiteMemory, None]:
    sqlite_memory = SQLiteMemory.__new__(SQLiteMemory)
    sqlite_memory.__init__(db_path=":memory:", _defer_initialization=True)
    try:
        sqlite_memory.disable_embedding()
        await sqlite_memory.initialize_async()
        async with await sqlite_memory.get_session_async() as session:
            connection = await session.connection()
            tables = await connection.run_sync(lambda sync_connection: inspect(sync_connection).get_table_names())
        assert {"PromptMemoryEntries", "EmbeddingData", "ScoreEntries", "SeedPromptEntries"} <= set(tables)
        yield sqlite_memory
    finally:
        await sqlite_memory.dispose_engine_async()


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

    async def set_system_prompt_async(
        self,
        *,
        system_prompt: str,
        conversation_id: str,
        attack_identifier: ComponentIdentifier | None = None,
        labels: dict[str, str] | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        if self._memory:
            (
                await self._memory.add_message_to_memory_async(
                    request=MessagePiece(
                        role="system",
                        original_value=system_prompt,
                        converted_value=system_prompt,
                        conversation_id=conversation_id,
                    ).to_message()
                )
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
