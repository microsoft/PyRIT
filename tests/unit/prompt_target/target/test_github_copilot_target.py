# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, NonCallableMagicMock, call, create_autospec, patch
from uuid import UUID, uuid4

import pytest
from unit.mocks import store_message

from pyrit.models import Message, MessagePiece, MessageScorable, ScoringExpectation
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import GitHubCopilotTarget
from pyrit.score import SelfAskTrueFalseScorer, TrueFalseQuestion

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from copilot.generated.session_events import SessionEvent

    from pyrit.memory import MemoryInterface

TARGET_LOGGER = "pyrit.prompt_target.github_copilot_target"


@pytest.fixture
def sdk() -> Any:
    return pytest.importorskip("copilot")


@pytest.fixture
def client(sdk: Any) -> Iterator[NonCallableMagicMock]:
    from copilot.client import GetStatusResponse

    session = _make_sdk_session(sdk=sdk, session_id="sdk-session-id")
    session.send_and_wait.return_value = _assistant_reply("HELLO")
    client = create_autospec(sdk.CopilotClient, instance=True)
    assert isinstance(client, NonCallableMagicMock)
    client.create_session.return_value = session
    client.get_status.return_value = GetStatusResponse(version="6.5.4", protocol_version=3)
    with patch.object(sdk, "CopilotClient", return_value=client):
        yield client


def _assistant_reply(text: str) -> SessionEvent:
    from copilot.generated.session_events import AssistantMessageData, SessionEvent, SessionEventType

    return SessionEvent(
        id=uuid4(),
        timestamp=datetime.now(UTC),
        type=SessionEventType.ASSISTANT_MESSAGE,
        data=AssistantMessageData(content=text, message_id="sdk-reply"),
    )


def _mock_session_storage(*, client: NonCallableMagicMock, sessions: set[str]) -> None:
    from copilot import SessionMetadata

    async def get_session_metadata_async(session_id: str) -> SessionMetadata | None:
        if session_id not in sessions:
            return None
        return SessionMetadata(
            session_id=session_id,
            start_time=datetime(2026, 1, 1, tzinfo=UTC),
            modified_time=datetime(2026, 1, 1, tzinfo=UTC),
            is_remote=False,
        )

    client.get_session_metadata.side_effect = get_session_metadata_async
    client.delete_session.side_effect = sessions.remove


def _make_sdk_session(
    *,
    sdk: Any,
    session_id: str,
) -> NonCallableMagicMock:
    session = create_autospec(sdk.CopilotSession, instance=True)
    assert isinstance(session, NonCallableMagicMock)
    session.session_id = session_id
    return session


def _user_message(
    *,
    original_value: str,
    converted_value: str | None = None,
    conversation_id: str | None = None,
) -> Message:
    return MessagePiece(
        role="user",
        conversation_id=conversation_id,
        original_value=original_value,
        converted_value=original_value if converted_value is None else converted_value,
    ).to_message()


async def _send_normalized_async(
    *,
    target: GitHubCopilotTarget,
    original_value: str,
    converted_value: str | None = None,
    conversation_id: str | None = None,
) -> Message:
    return await PromptNormalizer().send_prompt_async(
        message=_user_message(
            original_value=original_value,
            converted_value=converted_value,
        ),
        conversation_id=conversation_id,
        target=target,
    )


def _message_state(*, memory: MemoryInterface, conversation_id: str) -> list[tuple[str, str, str, str | None, str]]:
    return [
        (
            message.get_piece().role,
            message.get_piece().original_value,
            message.get_piece().converted_value,
            message.get_piece().conversation_id,
            message.get_piece().response_error,
        )
        for message in memory.get_conversation_messages(conversation_id=conversation_id)
    ]


def _message_roles_and_errors(*, memory: MemoryInterface, conversation_id: str) -> list[tuple[str, str]]:
    return [
        (message.get_piece().role, message.get_piece().response_error)
        for message in memory.get_conversation_messages(conversation_id=conversation_id)
    ]


def _message_values_and_errors(*, memory: MemoryInterface, conversation_id: str) -> list[tuple[str, str, str]]:
    return [
        (message.get_piece().role, message.get_piece().converted_value, message.get_piece().response_error)
        for message in memory.get_conversation_messages(conversation_id=conversation_id)
    ]


def _expected_session_configuration(*, system_message: dict[str, Any]) -> dict[str, Any]:
    from copilot.generated.rpc import RemoteSessionMode

    return {
        "model": "gpt-5-mini",
        "system_message": system_message,
        "remote_session": RemoteSessionMode.OFF,
        "available_tools": [],
        "skip_custom_instructions": True,
        "instruction_directories": [],
        "enable_host_git_operations": False,
        "enable_config_discovery": False,
        "organization_custom_instructions": "",
        "enable_on_demand_instruction_discovery": False,
        "infinite_sessions": {"enabled": False},
        "memory": {"enabled": False},
        "enable_session_store": False,
        "enable_file_hooks": False,
    }


async def _cancel_tasks_async(*tasks: asyncio.Task[Any] | None) -> None:
    for task in tasks:
        if task is not None and not task.done():
            task.cancel()
    await asyncio.gather(*[task for task in tasks if task is not None], return_exceptions=True)


def _assert_no_resource_release(client: NonCallableMagicMock) -> None:
    client.delete_session.assert_not_awaited()
    client.stop.assert_not_awaited()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("retain_session", [False, True], ids=["delete", "retain"])
async def test_normalizer_round_trip_and_retention_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
    sqlite_instance: MemoryInterface,
    caplog: pytest.LogCaptureFixture,
    retain_session: bool,
) -> None:
    conversation_id = str(uuid4())
    target = GitHubCopilotTarget(model_name="gpt-5-mini", retain_session=retain_session)
    with patch.object(sdk, "__version__", "9.8.7"), caplog.at_level(logging.INFO, logger=TARGET_LOGGER):
        response = await _send_normalized_async(
            target=target,
            original_value="Original text before conversion.",
            converted_value="Reply exactly HELLO.",
            conversation_id=conversation_id,
        )
        _assert_no_resource_release(client=client)
        await target.cleanup_target_async()

    assert response.get_piece().converted_value == "HELLO"
    assert _message_state(memory=sqlite_instance, conversation_id=conversation_id) == [
        ("user", "Original text before conversion.", "Reply exactly HELLO.", conversation_id, "none"),
        ("assistant", "HELLO", "HELLO", conversation_id, "none"),
    ]
    session = client.create_session.return_value
    session.send_and_wait.assert_awaited_once_with("Reply exactly HELLO.", timeout=60.0)
    session.on.return_value.assert_called_once_with()
    client.create_session.assert_awaited_once()
    client.get_session_metadata.assert_not_awaited()
    sdk.CopilotClient.assert_called_once_with(github_token=None, working_directory=None)
    requested_id = client.create_session.await_args.kwargs["session_id"]
    assert str(UUID(requested_id)) == requested_id
    assert requested_id != "sdk-session-id"
    records = [r.getMessage() for r in caplog.records if r.name == TARGET_LOGGER and r.levelno == logging.INFO]
    assert len(records) == (2 if retain_session else 1)
    assert records[0].startswith("Attempting Copilot session creation:")
    for field in (
        f"pyrit_conversation_id={conversation_id}",
        f"requested_sdk_session_id={requested_id}",
        "sdk_version=9.8.7",
        "runtime_version=6.5.4",
        "runtime_protocol_version=3",
        f"retain_session={retain_session}",
        "remote_mode=OFF",
    ):
        assert field in records[0]
    if retain_session:
        client.delete_session.assert_not_awaited()
        assert records[1] == "Retaining Copilot session sdk-session-id as requested; delete it manually."
    else:
        client.delete_session.assert_awaited_once_with("sdk-session-id")
    client.stop.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "initial_system_prompt",
    [pytest.param(None, id="default-customize"), pytest.param("initial system instructions", id="initial-replacement")],
)
async def test_normalizer_continues_native_session_across_turns_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
    sqlite_instance: MemoryInterface,
    initial_system_prompt: str | None,
) -> None:
    conversation_id = "native-two-turn-conversation"
    session = client.create_session.return_value
    session.session_id = "sdk-session-id"
    session.send_and_wait.side_effect = [_assistant_reply("FIRST"), _assistant_reply("SECOND")]
    target = GitHubCopilotTarget(model_name="gpt-5-mini")

    if initial_system_prompt is not None:
        target.set_system_prompt(system_prompt=initial_system_prompt, conversation_id=conversation_id)

    first_response = await _send_normalized_async(
        target=target,
        original_value="first original",
        converted_value="first prepared",
        conversation_id=conversation_id,
    )
    assert first_response.get_piece().converted_value == "FIRST"

    configuration = dict(client.create_session.await_args.kwargs)
    requested_session_id = configuration.pop("session_id")
    assert str(UUID(requested_session_id)) == requested_session_id
    assert configuration == _expected_session_configuration(
        system_message=(
            {"mode": "replace", "content": initial_system_prompt}
            if initial_system_prompt is not None
            else {
                "mode": "customize",
                "sections": {
                    "environment_context": {"action": "remove"},
                    "custom_instructions": {"action": "remove"},
                },
            }
        )
    )
    if initial_system_prompt is not None:
        with pytest.raises(RuntimeError, match="Conversation already exists"):
            target.set_system_prompt(system_prompt="different system instructions", conversation_id=conversation_id)

    second_response = await _send_normalized_async(
        target=target,
        original_value="second original",
        converted_value="second prepared",
        conversation_id=conversation_id,
    )
    assert second_response.get_piece().converted_value == "SECOND"
    assert session.send_and_wait.await_args_list == [
        call("first prepared", timeout=60.0),
        call("second prepared", timeout=60.0),
    ]
    client.create_session.assert_awaited_once()
    expected_messages = [
        ("user", "first original", "first prepared", conversation_id, "none"),
        ("assistant", "FIRST", "FIRST", conversation_id, "none"),
        ("user", "second original", "second prepared", conversation_id, "none"),
        ("assistant", "SECOND", "SECOND", conversation_id, "none"),
    ]
    if initial_system_prompt is not None:
        expected_messages.insert(0, ("system", initial_system_prompt, initial_system_prompt, conversation_id, "none"))
    assert _message_state(memory=sqlite_instance, conversation_id=conversation_id) == expected_messages
    _assert_no_resource_release(client=client)
    await target.cleanup_target_async()
    client.delete_session.assert_awaited_once_with(session.session_id)
    client.stop.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database")
async def test_distinct_conversations_progress_on_shared_client_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
) -> None:
    session_a = _make_sdk_session(sdk=sdk, session_id="sdk-session-a")
    session_b = _make_sdk_session(sdk=sdk, session_id="sdk-session-b")
    first_send_started = asyncio.Event()
    release_first_send = asyncio.Event()

    async def send_a_async(*_args: Any, **_kwargs: Any) -> Any:
        first_send_started.set()
        await release_first_send.wait()
        return _assistant_reply("A")

    session_a.send_and_wait.side_effect = send_a_async
    session_b.send_and_wait.return_value = _assistant_reply("B")
    client.create_session.side_effect = [session_a, session_b]
    target = GitHubCopilotTarget(model_name="gpt-5-mini")
    first_task = asyncio.create_task(
        target.send_prompt_async(
            message=_user_message(
                conversation_id="conversation-a",
                original_value="a original",
                converted_value="a prepared",
            )
        )
    )
    second_task: asyncio.Task[list[Message]] | None = None
    try:
        await asyncio.wait_for(first_send_started.wait(), timeout=2.0)
        second_task = asyncio.create_task(
            target.send_prompt_async(
                message=_user_message(
                    conversation_id="conversation-b",
                    original_value="b original",
                    converted_value="b prepared",
                )
            )
        )
        second_response = await asyncio.wait_for(second_task, timeout=2.0)
        assert not release_first_send.is_set()
        assert (second_response[0].get_piece().conversation_id, second_response[0].get_piece().converted_value) == (
            "conversation-b",
            "B",
        )

        release_first_send.set()
        first_response = await asyncio.wait_for(first_task, timeout=2.0)
        assert (first_response[0].get_piece().conversation_id, first_response[0].get_piece().converted_value) == (
            "conversation-a",
            "A",
        )
        _assert_no_resource_release(client=client)
        await target.cleanup_target_async()
    finally:
        release_first_send.set()
        await _cancel_tasks_async(first_task, second_task)
        with suppress(Exception):
            await asyncio.wait_for(target.cleanup_target_async(), timeout=2.0)

    sdk.CopilotClient.assert_called_once_with(github_token=None, working_directory=None)
    client.start.assert_awaited_once()
    client.get_status.assert_awaited_once()
    assert client.create_session.await_count == 2
    requested_session_ids = [entry.kwargs["session_id"] for entry in client.create_session.await_args_list]
    assert all(str(UUID(session_id)) == session_id for session_id in requested_session_ids)
    assert len(set(requested_session_ids)) == 2
    session_a.send_and_wait.assert_awaited_once_with("a prepared", timeout=60.0)
    session_b.send_and_wait.assert_awaited_once_with("b prepared", timeout=60.0)
    assert client.delete_session.await_args_list == [call(session_a.session_id), call(session_b.session_id)]
    client.stop.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database")
async def test_reset_conversation_releases_only_requested_session_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
    sqlite_instance: MemoryInterface,
) -> None:
    session_a = _make_sdk_session(sdk=sdk, session_id="sdk-session-a")
    session_a.send_and_wait.side_effect = [_assistant_reply("A1"), _assistant_reply("A_REOPENED")]
    session_b = _make_sdk_session(sdk=sdk, session_id="sdk-session-b")
    session_b.send_and_wait.side_effect = [_assistant_reply("B1"), _assistant_reply("B2")]
    client.create_session.side_effect = [session_a, session_b]
    target = GitHubCopilotTarget(model_name="gpt-5-mini", retain_session=True)

    await _send_normalized_async(
        target=target,
        original_value="a original",
        converted_value="a prepared",
        conversation_id="conversation-a",
    )
    await _send_normalized_async(
        target=target,
        original_value="b original",
        converted_value="b prepared",
        conversation_id="conversation-b",
    )
    memory_before_reset = {
        conversation_id: _message_values_and_errors(memory=sqlite_instance, conversation_id=conversation_id)
        for conversation_id in ("conversation-a", "conversation-b")
    }

    await target.reset_conversation_async(conversation_id="conversation-a")
    assert session_a.disconnect.await_count == 1
    _assert_no_resource_release(client=client)
    session_b.disconnect.assert_not_awaited()
    memory_after_reset = {
        conversation_id: _message_values_and_errors(memory=sqlite_instance, conversation_id=conversation_id)
        for conversation_id in ("conversation-a", "conversation-b")
    }
    assert memory_after_reset == memory_before_reset

    await target.reset_conversation_async(conversation_id="conversation-a")
    assert session_a.disconnect.await_count == 1

    with pytest.raises(Exception, match="Error sending prompt with conversation ID:") as reset_error:
        await _send_normalized_async(
            target=target,
            original_value="a retry original",
            converted_value="a retry prepared",
            conversation_id="conversation-a",
        )
    assert isinstance(reset_error.value.__cause__, RuntimeError)
    assert "retired" in str(reset_error.value.__cause__).lower()

    response_b = await _send_normalized_async(
        target=target,
        original_value="b second original",
        converted_value="b second prepared",
        conversation_id="conversation-b",
    )
    assert response_b.get_piece().converted_value == "B2"
    assert client.create_session.await_count == 2
    session_a.send_and_wait.assert_awaited_once_with("a prepared", timeout=60.0)
    session_b.send_and_wait.assert_has_awaits(
        [
            call("b prepared", timeout=60.0),
            call("b second prepared", timeout=60.0),
        ]
    )
    client.delete_session.assert_not_awaited()
    await target.cleanup_target_async()
    client.delete_session.assert_not_awaited()
    client.stop.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database")
async def test_reset_waits_for_in_progress_cleanup_release_async(
    *,
    client: NonCallableMagicMock,
) -> None:
    session = client.create_session.return_value
    target = GitHubCopilotTarget(model_name="gpt-5-mini", retain_session=True)
    await _send_normalized_async(
        target=target,
        original_value="a original",
        converted_value="a prepared",
        conversation_id="conversation-a",
    )
    disconnect_started = asyncio.Event()
    release_disconnect = asyncio.Event()

    async def disconnect_async() -> None:
        disconnect_started.set()
        await release_disconnect.wait()

    session.disconnect.side_effect = disconnect_async
    cleanup_task = asyncio.create_task(target.cleanup_target_async())
    reset_task: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(disconnect_started.wait(), timeout=2.0)
        reset_task = asyncio.create_task(target.reset_conversation_async(conversation_id="conversation-a"))
        await asyncio.sleep(0)
        assert not reset_task.done()

        release_disconnect.set()
        await asyncio.wait_for(cleanup_task, timeout=2.0)
        await asyncio.wait_for(reset_task, timeout=2.0)
        session.disconnect.assert_awaited_once()
        client.delete_session.assert_not_awaited()
        client.stop.assert_awaited_once()
    finally:
        release_disconnect.set()
        await _cancel_tasks_async(cleanup_task, reset_task)


@pytest.mark.usefixtures("patch_central_database")
async def test_reset_waits_for_conversation_creation_async(
    *,
    client: NonCallableMagicMock,
) -> None:
    session = client.create_session.return_value
    session.session_id = "sdk-session-a"
    session.send_and_wait.return_value = _assistant_reply("FIRST")
    create_started = asyncio.Event()
    release_creation = asyncio.Event()

    async def create_session_async(*_args: Any, **_kwargs: Any) -> Any:
        create_started.set()
        await release_creation.wait()
        return session

    client.create_session.side_effect = create_session_async
    target = GitHubCopilotTarget(model_name="gpt-5-mini")
    conversation_id = "provisioning-conversation"
    first_task = asyncio.create_task(
        target.send_prompt_async(
            message=_user_message(
                conversation_id=conversation_id,
                original_value="first original",
                converted_value="first prepared",
            )
        )
    )
    reset_task: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(create_started.wait(), timeout=2.0)
        reset_task = asyncio.create_task(target.reset_conversation_async(conversation_id=conversation_id))
        await asyncio.sleep(0)
        assert not reset_task.done()

        release_creation.set()
        first_response = await asyncio.wait_for(first_task, timeout=2.0)
        await asyncio.wait_for(reset_task, timeout=2.0)
        assert first_response[0].get_piece().converted_value == "FIRST"
        session.send_and_wait.assert_awaited_once_with("first prepared", timeout=60.0)
        client.create_session.assert_awaited_once()
        client.delete_session.assert_awaited_once_with(session.session_id)
        client.stop.assert_not_awaited()

        await target.cleanup_target_async()
        client.stop.assert_awaited_once()
    finally:
        release_creation.set()
        await _cancel_tasks_async(first_task, reset_task)
        with suppress(Exception):
            await asyncio.wait_for(target.cleanup_target_async(), timeout=2.0)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "reset_before_cleanup",
    [pytest.param(True, id="explicit-reset"), pytest.param(False, id="whole-cleanup-release")],
)
async def test_repeated_reset_does_not_join_unrelated_cleanup_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
    reset_before_cleanup: bool,
) -> None:
    session_a = _make_sdk_session(sdk=sdk, session_id="sdk-session-a")
    session_a.send_and_wait.return_value = _assistant_reply("A")
    session_b = _make_sdk_session(sdk=sdk, session_id="sdk-session-b")
    session_b.send_and_wait.return_value = _assistant_reply("B")
    client.create_session.side_effect = [session_a, session_b]
    target = GitHubCopilotTarget(model_name="gpt-5-mini", retain_session=True)

    for conversation_id, prompt in (("conversation-a", "a prepared"), ("conversation-b", "b prepared")):
        await target.send_prompt_async(
            message=_user_message(
                conversation_id=conversation_id,
                original_value=prompt,
            )
        )
    if reset_before_cleanup:
        await target.reset_conversation_async(conversation_id="conversation-a")
        session_a.disconnect.assert_awaited_once()

    disconnect_started = asyncio.Event()
    release_disconnect = asyncio.Event()

    async def disconnect_b_async() -> None:
        disconnect_started.set()
        await release_disconnect.wait()
        raise RuntimeError("B disconnect failed")

    session_b.disconnect.side_effect = disconnect_b_async
    cleanup_task = asyncio.create_task(target.cleanup_target_async())
    reset_task: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(disconnect_started.wait(), timeout=2.0)
        if not reset_before_cleanup:
            session_a.disconnect.assert_awaited_once()
        reset_task = asyncio.create_task(target.reset_conversation_async(conversation_id="conversation-a"))
        await asyncio.sleep(0)
        assert reset_task.done()
        await reset_task
        assert session_a.disconnect.await_count == 1
        client.stop.assert_not_awaited()

        release_disconnect.set()
        with pytest.raises(RuntimeError, match="B disconnect failed"):
            await asyncio.wait_for(cleanup_task, timeout=2.0)
        session_b.disconnect.assert_awaited_once()
        client.stop.assert_awaited_once()
    finally:
        release_disconnect.set()
        await _cancel_tasks_async(cleanup_task, reset_task)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "failed_conversation",
    [
        pytest.param("conversation-a", id="selected-session-release-fails"),
        pytest.param("conversation-b", id="unrelated-session-release-fails"),
    ],
)
async def test_reset_during_cleanup_propagates_only_selected_session_failure_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
    failed_conversation: str,
) -> None:
    session_a = _make_sdk_session(sdk=sdk, session_id="sdk-session-a")
    session_a.send_and_wait.return_value = _assistant_reply("A")
    session_b = _make_sdk_session(sdk=sdk, session_id="sdk-session-b")
    session_b.send_and_wait.return_value = _assistant_reply("B")
    client.create_session.side_effect = [session_a, session_b]
    target = GitHubCopilotTarget(model_name="gpt-5-mini", retain_session=True)

    for conversation_id, prompt in (("conversation-a", "A"), ("conversation-b", "B")):
        await target.send_prompt_async(
            message=_user_message(conversation_id=conversation_id, original_value=prompt),
        )

    a_release_started = asyncio.Event()
    release_a = asyncio.Event()
    a_release_finished = asyncio.Event()
    b_release_started = asyncio.Event()
    release_b = asyncio.Event()
    a_failure = RuntimeError("selected session A release failed")
    b_failure = RuntimeError("unrelated session B release failed")

    async def disconnect_a_async() -> None:
        a_release_started.set()
        try:
            await release_a.wait()
            if failed_conversation == "conversation-a":
                raise a_failure
        finally:
            a_release_finished.set()

    async def disconnect_b_async() -> None:
        b_release_started.set()
        await release_b.wait()
        if failed_conversation == "conversation-b":
            raise b_failure

    session_a.disconnect.side_effect = disconnect_a_async
    session_b.disconnect.side_effect = disconnect_b_async
    cleanup_task = asyncio.create_task(target.cleanup_target_async())
    reset_task: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(a_release_started.wait(), timeout=2.0)
        reset_task = asyncio.create_task(target.reset_conversation_async(conversation_id="conversation-a"))
        await asyncio.sleep(0)
        assert not reset_task.done()

        release_a.set()
        await asyncio.wait_for(a_release_finished.wait(), timeout=2.0)
        await asyncio.wait_for(b_release_started.wait(), timeout=2.0)
        assert a_release_finished.is_set()
        release_b.set()

        expected_cleanup_failure = a_failure if failed_conversation == "conversation-a" else b_failure
        with pytest.raises(RuntimeError) as cleanup_error:
            await asyncio.wait_for(cleanup_task, timeout=2.0)
        assert cleanup_error.value is expected_cleanup_failure

        if failed_conversation == "conversation-a":
            with pytest.raises(RuntimeError) as reset_error:
                await asyncio.wait_for(reset_task, timeout=2.0)
            assert reset_error.value is a_failure
        else:
            await asyncio.wait_for(reset_task, timeout=2.0)

        assert client.create_session.await_count == 2
        session_a.send_and_wait.assert_awaited_once()
        session_b.send_and_wait.assert_awaited_once()
        session_a.disconnect.assert_awaited_once()
        session_b.disconnect.assert_awaited_once()
        client.stop.assert_awaited_once()
    finally:
        release_a.set()
        release_b.set()
        tasks = {cleanup_task}
        if reset_task is not None:
            tasks.add(reset_task)
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.usefixtures("patch_central_database")
async def test_normalizer_rejects_retired_conversation_but_allows_fresh_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
    sqlite_instance: MemoryInterface,
) -> None:
    session_a = _make_sdk_session(sdk=sdk, session_id="sdk-session-a")
    session_a.send_and_wait.side_effect = TimeoutError("ambiguous mock send")

    session_b = _make_sdk_session(sdk=sdk, session_id="sdk-session-b")
    session_b.send_and_wait.return_value = _assistant_reply("FRESH")
    client.create_session.side_effect = [session_a, session_b]
    target = GitHubCopilotTarget(model_name="gpt-5-mini")

    conversation_a = "retired-conversation"
    with pytest.raises(Exception, match="Error sending prompt with conversation ID:") as first_error:
        await _send_normalized_async(
            target=target,
            original_value="ambiguous original",
            converted_value="ambiguous prepared",
            conversation_id=conversation_a,
        )
    assert isinstance(first_error.value.__cause__, TimeoutError)
    assert str(first_error.value.__cause__) == "ambiguous mock send"

    with pytest.raises(Exception, match="Error sending prompt with conversation ID:") as retired_error:
        await _send_normalized_async(
            target=target,
            original_value="retry original",
            converted_value="retry prepared",
            conversation_id=conversation_a,
        )
    assert isinstance(retired_error.value.__cause__, RuntimeError)
    assert "retired" in str(retired_error.value.__cause__).lower()

    conversation_b = "fresh-conversation"
    response_b = await _send_normalized_async(
        target=target,
        original_value="fresh original",
        converted_value="fresh prepared",
        conversation_id=conversation_b,
    )
    assert response_b.get_piece().converted_value == "FRESH"
    assert _message_roles_and_errors(memory=sqlite_instance, conversation_id=conversation_a) == [
        ("user", "none"),
        ("assistant", "processing"),
        ("user", "none"),
        ("assistant", "processing"),
    ]
    assert _message_values_and_errors(memory=sqlite_instance, conversation_id=conversation_b) == [
        ("user", "fresh prepared", "none"),
        ("assistant", "FRESH", "none"),
    ]

    sdk.CopilotClient.assert_called_once_with(github_token=None, working_directory=None)
    client.start.assert_awaited_once()
    client.get_status.assert_awaited_once()
    assert client.create_session.await_count == 2
    requested_session_ids = [entry.kwargs["session_id"] for entry in client.create_session.await_args_list]
    assert all(str(UUID(session_id)) == session_id for session_id in requested_session_ids)
    assert len(set(requested_session_ids)) == 2
    session_a.send_and_wait.assert_awaited_once_with("ambiguous prepared", timeout=60.0)
    session_b.send_and_wait.assert_awaited_once_with("fresh prepared", timeout=60.0)
    assert client.delete_session.await_args_list == [call("sdk-session-a")]
    client.stop.assert_not_awaited()
    await target.cleanup_target_async()
    assert client.delete_session.await_args_list == [call("sdk-session-a"), call("sdk-session-b")]
    client.stop.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database")
async def test_self_ask_true_false_uses_fresh_copilot_session_after_invalid_json_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
) -> None:
    failed_session = _make_sdk_session(sdk=sdk, session_id="malformed-judge-session")
    failed_session.send_and_wait.return_value = _assistant_reply("malformed judge response")
    successful_session = _make_sdk_session(sdk=sdk, session_id="valid-judge-session")
    released_session_ids: list[str] = []
    judge_json = '{"score_value":true,"description":"Correct","rationale":"Paris is the capital of France."}'

    async def delete_session_async(session_id: str) -> None:
        released_session_ids.append(session_id)

    async def send_valid_judge_reply_async(prompt: str, *, timeout: float) -> SessionEvent:
        assert released_session_ids == [failed_session.session_id]
        return _assistant_reply(judge_json)

    successful_session.send_and_wait.side_effect = send_valid_judge_reply_async
    client.create_session.side_effect = [failed_session, successful_session]
    client.delete_session.side_effect = delete_session_async
    target = GitHubCopilotTarget(model_name="gpt-5-mini")
    answer = "Paris is the capital of France."
    saved_answer = store_message(
        MessagePiece(
            role="assistant",
            conversation_id=str(uuid4()),
            original_value=answer,
        ).to_message()
    )
    input_piece = saved_answer.get_piece()
    input_scorable = MessageScorable.from_message(saved_answer)

    try:
        question = TrueFalseQuestion(
            category="capital correctness",
            true_description="The response correctly identifies Paris as the capital of France.",
            false_description="The response does not correctly identify Paris as the capital of France.",
        )
        scorer = SelfAskTrueFalseScorer.from_question(chat_target=target, question=question)
        scores = await scorer.score_async(
            scorable=input_scorable,
            expectation=ScoringExpectation(objective="Name France's capital"),
        )

        assert len(scores) == 1
        assert scores[0].get_value() is True

        failed_session.send_and_wait.assert_awaited_once()
        successful_session.send_and_wait.assert_awaited_once()
        client.create_session.assert_awaited()
        assert client.create_session.await_count == 2
        requested_session_ids = [entry.kwargs["session_id"] for entry in client.create_session.await_args_list]
        assert all(str(UUID(session_id)) == session_id for session_id in requested_session_ids)
        assert len(set(requested_session_ids)) == 2
        session_configurations = [entry.kwargs for entry in client.create_session.await_args_list]
        assert session_configurations[0]["system_message"] == session_configurations[1]["system_message"]
        assert session_configurations[0]["system_message"]["mode"] == "replace"
    finally:
        await target.cleanup_target_async()

    assert client.delete_session.await_args_list == [
        call(failed_session.session_id),
        call(successful_session.session_id),
    ]
    client.stop.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database")
async def test_cancelled_send_keeps_retirement_owned_until_cleanup_async(
    *,
    client: NonCallableMagicMock,
) -> None:
    session = client.create_session.return_value
    session.send_and_wait.side_effect = TimeoutError("ambiguous mock send")
    delete_started = asyncio.Event()
    release_delete = asyncio.Event()
    delete_tasks: list[asyncio.Task[Any]] = []
    delete_count = 0

    async def delete_session_async(session_id: str) -> None:
        nonlocal delete_count
        delete_count += 1
        current_task = asyncio.current_task()
        if current_task is not None:
            delete_tasks.append(current_task)
        if delete_count == 1:
            delete_started.set()
        await release_delete.wait()

    client.delete_session.side_effect = delete_session_async
    target = GitHubCopilotTarget(model_name="gpt-5-mini")
    send_task = asyncio.create_task(
        target.send_prompt_async(
            message=_user_message(
                conversation_id="cancelled-retirement",
                original_value="ambiguous request",
            )
        )
    )
    cleanup_started = asyncio.Event()

    async def cleanup_target_for_test_async() -> None:
        cleanup_started.set()
        await target.cleanup_target_async()

    cleanup_task: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(delete_started.wait(), timeout=2.0)
        send_task.cancel()
        cleanup_task = asyncio.create_task(cleanup_target_for_test_async())
        await asyncio.wait_for(cleanup_started.wait(), timeout=2.0)

        assert not send_task.done()
        assert not cleanup_task.done()
        client.delete_session.assert_awaited_once_with(session.session_id)
        client.stop.assert_not_awaited()

        release_delete.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(send_task), timeout=2.0)
        await asyncio.wait_for(cleanup_task, timeout=2.0)
        client.delete_session.assert_awaited_once_with(session.session_id)
        client.stop.assert_awaited_once()
        assert all(task.done() for task in delete_tasks)
    finally:
        release_delete.set()
        tasks = {send_task, *delete_tasks}
        if cleanup_task is not None:
            tasks.add(cleanup_task)
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=2.0)


@pytest.mark.usefixtures("patch_central_database")
async def test_cleanup_rejects_queued_turn_and_drains_active_send_async(
    *,
    client: NonCallableMagicMock,
) -> None:
    session = client.create_session.return_value
    first_send_started = asyncio.Event()
    release_first_send = asyncio.Event()

    async def send_and_wait_async(*_args: Any, **_kwargs: Any) -> Any:
        first_send_started.set()
        await release_first_send.wait()
        return _assistant_reply("FIRST")

    session.send_and_wait.side_effect = send_and_wait_async
    target = GitHubCopilotTarget(model_name="gpt-5-mini")
    conversation_id = "queued-cleanup-conversation"
    first_task = asyncio.create_task(
        target.send_prompt_async(
            message=_user_message(conversation_id=conversation_id, original_value="first"),
        )
    )
    queued_task: asyncio.Task[list[Message]] | None = None
    cleanup_task: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(first_send_started.wait(), timeout=2.0)
        queued_task = asyncio.create_task(
            target.send_prompt_async(
                message=_user_message(conversation_id=conversation_id, original_value="queued"),
            )
        )
        await asyncio.sleep(0)
        cleanup_task = asyncio.create_task(target.cleanup_target_async())
        await asyncio.sleep(0)

        with pytest.raises(RuntimeError, match="cleaned up"):
            await asyncio.wait_for(
                target.send_prompt_async(
                    message=_user_message(
                        conversation_id="fresh-after-cleanup",
                        original_value="fresh",
                    )
                ),
                timeout=2.0,
            )
        _assert_no_resource_release(client=client)

        release_first_send.set()
        first_response = await asyncio.wait_for(first_task, timeout=2.0)
        assert first_response[0].get_piece().converted_value == "FIRST"
        assert queued_task is not None
        with pytest.raises(RuntimeError, match="cleaned up"):
            await asyncio.wait_for(queued_task, timeout=2.0)
        assert cleanup_task is not None
        await asyncio.wait_for(cleanup_task, timeout=2.0)
    finally:
        release_first_send.set()
        await _cancel_tasks_async(first_task, queued_task, cleanup_task)

    session.send_and_wait.assert_awaited_once_with("first", timeout=60.0)
    client.create_session.assert_awaited_once()
    client.delete_session.assert_awaited_once_with(session.session_id)
    client.stop.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database", "sdk")
def test_target_advertises_native_text_only_capabilities() -> None:
    capabilities = GitHubCopilotTarget(model_name="gpt-4o").capabilities
    assert capabilities.supports_multi_turn is True
    assert capabilities.supports_system_prompt is True
    assert capabilities.supports_multi_message_pieces is False
    assert capabilities.input_modalities == frozenset({frozenset({"text"})})
    assert capabilities.output_modalities == frozenset({frozenset({"text"})})


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("capture_before", "requested_model", "expected_model", "expected_error"),
    [
        pytest.param(True, "gpt-5.4", "gpt-5-mini", RuntimeError, id="different-after-capture"),
        pytest.param(False, "gpt-5.4", "gpt-5.4", None, id="different-before-capture"),
        pytest.param(True, "gpt-5-mini", "gpt-5-mini", None, id="same-after-capture"),
        pytest.param(False, "   ", "gpt-5-mini", ValueError, id="blank-before-capture"),
        pytest.param(True, "   ", "gpt-5-mini", ValueError, id="blank-after-capture"),
    ],
)
def test_set_model_name_boundaries(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
    capture_before: bool,
    requested_model: str,
    expected_model: str,
    expected_error: type[Exception] | None,
) -> None:
    target = GitHubCopilotTarget(model_name="gpt-5-mini")
    captured_identifier = target.get_identifier() if capture_before else None

    if expected_error is None:
        target.set_model_name(model_name=requested_model)
    else:
        with pytest.raises(expected_error):
            target.set_model_name(model_name=requested_model)

    identity = target.get_identifier()
    assert identity.params["model_name"] == expected_model
    if captured_identifier is not None:
        assert identity == captured_identifier
    sdk.CopilotClient.assert_not_called()
    client.start.assert_not_awaited()
    client.create_session.assert_not_awaited()


@pytest.mark.usefixtures("patch_central_database")
async def test_direct_send_captures_model_identity_before_startup_async(
    *,
    client: NonCallableMagicMock,
) -> None:
    startup_entered = asyncio.Event()
    release_startup = asyncio.Event()

    async def start_async() -> None:
        startup_entered.set()
        await release_startup.wait()

    client.start.side_effect = start_async
    target = GitHubCopilotTarget(model_name="gpt-5-mini")
    first_task = asyncio.create_task(
        target.send_prompt_async(
            message=_user_message(
                conversation_id="direct-model-capture",
                original_value="first original",
                converted_value="first prepared",
            )
        )
    )
    try:
        await asyncio.wait_for(startup_entered.wait(), timeout=2.0)
        with pytest.raises(RuntimeError, match="new target"):
            target.set_model_name(model_name="gpt-5.4")

        release_startup.set()
        response = await asyncio.wait_for(first_task, timeout=2.0)
        assert response[0].get_piece().converted_value == "HELLO"
        assert client.create_session.await_args.kwargs["model"] == "gpt-5-mini"
        with pytest.raises(RuntimeError, match="new target"):
            target.set_model_name(model_name="gpt-5.4")
        assert target.get_identifier().params["model_name"] == "gpt-5-mini"
        await target.cleanup_target_async()
    finally:
        release_startup.set()
        await _cancel_tasks_async(first_task)
        with suppress(Exception):
            await asyncio.wait_for(target.cleanup_target_async(), timeout=2.0)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("failure_stage", ["start", "status", "send", "stop"])
async def test_normalizer_surfaces_lifecycle_failures_async(
    *,
    client: NonCallableMagicMock,
    sqlite_instance: MemoryInterface,
    failure_stage: str,
) -> None:
    from copilot.client import StopError

    session = client.create_session.return_value
    error = (
        ExceptionGroup("SDK shutdown failed", [StopError(message="Synthetic shutdown failure")])
        if failure_stage == "stop"
        else RuntimeError(f"Synthetic {failure_stage} failure")
    )
    target = GitHubCopilotTarget(model_name="gpt-5-mini")
    conversation_id = str(uuid4())

    if failure_stage == "stop":
        response = await _send_normalized_async(
            target=target,
            original_value="Reply exactly HELLO.",
            conversation_id=conversation_id,
        )
        assert response.get_piece().response_error == "none"
        client.stop.side_effect = error
        with pytest.raises(Exception) as exc_info:
            await target.cleanup_target_async()
        assert exc_info.value is error
        client.start.assert_awaited_once()
        client.get_status.assert_awaited_once()
        client.create_session.assert_awaited_once()
        session.send_and_wait.assert_awaited_once()
        client.delete_session.assert_awaited_once_with("sdk-session-id")
        client.stop.assert_awaited_once()
        assert _message_roles_and_errors(memory=sqlite_instance, conversation_id=conversation_id) == [
            ("user", "none"),
            ("assistant", "none"),
        ]
        return

    operation = {
        "start": client.start,
        "status": client.get_status,
        "send": session.send_and_wait,
    }[failure_stage]
    operation.side_effect = error
    with pytest.raises(Exception, match="Error sending prompt with conversation ID:") as exc_info:
        await _send_normalized_async(
            target=target,
            original_value="Reply exactly HELLO.",
            conversation_id=conversation_id,
        )

    assert exc_info.value.__cause__ is error
    assert _message_roles_and_errors(memory=sqlite_instance, conversation_id=conversation_id) == [
        ("user", "none"),
        ("assistant", "processing"),
    ]
    client.start.assert_awaited_once()
    client.get_session_metadata.assert_not_awaited()
    if failure_stage == "start":
        client.get_status.assert_not_awaited()
        client.stop.assert_awaited_once()
    else:
        client.get_status.assert_awaited_once()
    if failure_stage in ("start", "status"):
        client.create_session.assert_not_awaited()
        session.send_and_wait.assert_not_awaited()
        client.delete_session.assert_not_awaited()
    else:
        client.create_session.assert_awaited_once()
        session.send_and_wait.assert_awaited_once()
        client.delete_session.assert_awaited_once_with("sdk-session-id")
        session.on.return_value.assert_called_once_with()
        client.stop.assert_not_awaited()
    session.send.assert_not_awaited()
    await target.cleanup_target_async()
    if failure_stage == "send":
        client.stop.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database")
async def test_normalizer_surfaces_dispatch_timeout_and_cleans_up_without_replay_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
) -> None:
    session = client.create_session.return_value
    target = GitHubCopilotTarget(model_name="gpt-5-mini", response_timeout_seconds=0.01)

    async def stall_send_async(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.Event().wait()

    session.send.side_effect = stall_send_async
    session.send_and_wait.side_effect = partial(sdk.CopilotSession.send_and_wait, session)
    with pytest.raises(Exception, match="Error sending prompt with conversation ID:") as exc_info:
        # A bare watchdog TimeoutError must not satisfy the normalizer-wrapped failure.
        await asyncio.wait_for(
            _send_normalized_async(target=target, original_value="Reply exactly HELLO."),
            timeout=2.0,
        )
    assert isinstance(exc_info.value.__cause__, TimeoutError)
    session.send.assert_awaited_once()
    client.delete_session.assert_awaited_once_with("sdk-session-id")
    client.stop.assert_not_awaited()
    await target.cleanup_target_async()
    client.stop.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("invalid_reply", ["empty", "subagent", "absent", "non-assistant"])
async def test_normalizer_rejects_invalid_reply_async(
    *,
    client: NonCallableMagicMock,
    sqlite_instance: MemoryInterface,
    invalid_reply: str,
) -> None:
    from copilot.generated.session_events import SessionEventType, SessionIdleData

    reply = _assistant_reply("HELLO")
    session = client.create_session.return_value
    session.send_and_wait.return_value = {
        "empty": _assistant_reply(""),
        "subagent": replace(reply, agent_id="sdk-subagent-id"),
        "absent": None,
        "non-assistant": replace(reply, type=SessionEventType.SESSION_IDLE, data=SessionIdleData(aborted=False)),
    }[invalid_reply]
    conversation_id = str(uuid4())
    target = GitHubCopilotTarget(model_name="gpt-5-mini")
    with pytest.raises(Exception, match="Error sending prompt with conversation ID:") as exc_info:
        await _send_normalized_async(
            target=target,
            original_value="Reply exactly HELLO.",
            conversation_id=conversation_id,
        )
    assert isinstance(exc_info.value.__cause__, ValueError)
    assert _message_roles_and_errors(memory=sqlite_instance, conversation_id=conversation_id) == [
        ("user", "none"),
        ("assistant", "processing"),
    ]
    session.send_and_wait.assert_awaited_once()
    session.on.return_value.assert_called_once_with()
    client.delete_session.assert_awaited_once_with("sdk-session-id")
    client.stop.assert_not_awaited()
    await target.cleanup_target_async()
    client.stop.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("event_stream", "expected_error"),
    [("aborted-idle", "abort"), ("abort-then-idle", "abort"), ("tool-then-idle", "tool")],
    ids=["aborted-idle", "abort-then-idle", "tool-then-idle"],
)
async def test_normalizer_rejects_unsafe_events_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
    sqlite_instance: MemoryInterface,
    event_stream: str,
    expected_error: str,
) -> None:
    from copilot.generated.session_events import AbortData, SessionEventType, SessionIdleData, ToolExecutionStartData

    session = client.create_session.return_value
    reply = _assistant_reply("HELLO")
    events = {
        "aborted-idle": [
            reply,
            replace(reply, id=uuid4(), type=SessionEventType.SESSION_IDLE, data=SessionIdleData(aborted=True)),
        ],
        "abort-then-idle": [
            reply,
            replace(
                reply,
                id=uuid4(),
                type=SessionEventType.ABORT,
                data=AbortData.from_dict({"reason": "user_initiated"}),
            ),
            replace(reply, id=uuid4(), type=SessionEventType.SESSION_IDLE, data=SessionIdleData(aborted=None)),
        ],
        "tool-then-idle": [
            replace(
                reply,
                id=uuid4(),
                type=SessionEventType.TOOL_EXECUTION_START,
                data=ToolExecutionStartData(
                    tool_call_id="synthetic-tool-call", tool_name="benign_test_tool", arguments={"text": "HELLO"}
                ),
            ),
            reply,
            replace(reply, id=uuid4(), type=SessionEventType.SESSION_IDLE, data=SessionIdleData(aborted=False)),
        ],
    }[event_stream]
    handlers: list[Callable[[SessionEvent], None]] = []

    def subscribe(handler: Callable[[SessionEvent], None]) -> Callable[[], None]:
        handlers.append(handler)
        return partial(handlers.remove, handler)

    async def send_events_async(*_args: Any, **_kwargs: Any) -> str:
        for event in events:
            for handler in tuple(handlers):
                handler(event)
        return "sdk-request-id"

    session.on.side_effect = subscribe
    session.send.side_effect = send_events_async
    session.send_and_wait.side_effect = partial(sdk.CopilotSession.send_and_wait, session)
    conversation_id = str(uuid4())
    target = GitHubCopilotTarget(model_name="gpt-5-mini", response_timeout_seconds=1.0)
    with pytest.raises(Exception, match="Error sending prompt with conversation ID:") as exc_info:
        await _send_normalized_async(
            target=target,
            original_value="Reply exactly HELLO.",
            conversation_id=conversation_id,
        )
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert expected_error in str(exc_info.value.__cause__).lower()
    assert _message_roles_and_errors(memory=sqlite_instance, conversation_id=conversation_id) == [
        ("user", "none"),
        ("assistant", "processing"),
    ]
    session.send.assert_awaited_once()
    client.delete_session.assert_awaited_once_with("sdk-session-id")
    client.stop.assert_not_awaited()
    await target.cleanup_target_async()
    client.stop.assert_awaited_once()
    assert not handlers


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("retain_session", [False, True], ids=["delete", "retain"])
async def test_normalizer_cleans_up_partial_creation_async(
    *,
    client: NonCallableMagicMock,
    sqlite_instance: MemoryInterface,
    caplog: pytest.LogCaptureFixture,
    retain_session: bool,
) -> None:
    session = client.create_session.return_value
    sessions = {"unrelated-session-id"}
    allocated_session_id = ""
    creation_error = RuntimeError("Copilot post-create options update failed")

    async def create_session_async(*, session_id: str = "sdk-generated-session-id", **_kwargs: Any) -> None:
        nonlocal allocated_session_id
        allocated_session_id = session_id
        sessions.add(session_id)
        raise creation_error

    client.create_session.side_effect = create_session_async
    _mock_session_storage(client=client, sessions=sessions)
    conversation_id = str(uuid4())
    target = GitHubCopilotTarget(model_name="gpt-5-mini", retain_session=retain_session)
    with caplog.at_level(logging.INFO, logger=TARGET_LOGGER):
        with pytest.raises(Exception, match="Error sending prompt with conversation ID:") as exc_info:
            await _send_normalized_async(
                target=target,
                original_value="Reply exactly HELLO.",
                conversation_id=conversation_id,
            )

    assert exc_info.value.__cause__ is creation_error
    assert allocated_session_id == client.create_session.await_args.kwargs["session_id"]
    assert str(UUID(allocated_session_id)) == allocated_session_id
    assert allocated_session_id != "sdk-session-id"
    client.create_session.assert_awaited_once()
    client.get_session_metadata.assert_awaited_once_with(allocated_session_id)
    session.send_and_wait.assert_not_awaited()
    session.send.assert_not_awaited()
    client.stop.assert_not_awaited()
    await target.cleanup_target_async()
    client.stop.assert_awaited_once()
    assert _message_roles_and_errors(memory=sqlite_instance, conversation_id=conversation_id) == [
        ("user", "none"),
        ("assistant", "processing"),
    ]
    retained_logs = [
        r.getMessage()
        for r in caplog.records
        if r.name == TARGET_LOGGER
        and r.levelno == logging.INFO
        and r.getMessage().startswith("Retaining Copilot session ")
    ]
    if retain_session:
        client.delete_session.assert_not_awaited()
        assert sessions == {"unrelated-session-id", allocated_session_id}
        assert retained_logs == [f"Retaining Copilot session {allocated_session_id} as requested; delete it manually."]
    else:
        client.delete_session.assert_awaited_once_with(allocated_session_id)
        assert sessions == {"unrelated-session-id"}
        assert not retained_logs


@pytest.mark.usefixtures("patch_central_database")
async def test_normalizer_deletes_owned_session_when_creation_is_cancelled_after_allocation_async(
    client: NonCallableMagicMock,
) -> None:
    session = client.create_session.return_value
    sessions = {"unrelated-session-id"}
    allocated = asyncio.Event()

    async def create_session_async(*, session_id: str = "sdk-generated-session-id", **_kwargs: Any) -> None:
        sessions.add(session_id)
        allocated.set()
        await asyncio.Event().wait()

    client.create_session.side_effect = create_session_async
    _mock_session_storage(client=client, sessions=sessions)
    target = GitHubCopilotTarget(model_name="gpt-5-mini")
    request_task = asyncio.create_task(_send_normalized_async(target=target, original_value="Reply exactly HELLO."))
    try:
        await asyncio.wait_for(allocated.wait(), timeout=2.0)
        request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_task
    finally:
        await _cancel_tasks_async(request_task)

    client.create_session.assert_awaited_once()
    allocated_id = client.create_session.await_args.kwargs["session_id"]
    client.get_session_metadata.assert_awaited_once_with(allocated_id)
    client.delete_session.assert_awaited_once_with(allocated_id)
    session.send_and_wait.assert_not_awaited()
    session.send.assert_not_awaited()
    client.stop.assert_not_awaited()
    await target.cleanup_target_async()
    client.stop.assert_awaited_once()
    assert sessions == {"unrelated-session-id"}


@pytest.mark.usefixtures("patch_central_database")
async def test_normalizer_stops_owned_client_when_startup_is_cancelled_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
) -> None:
    session = client.create_session.return_value
    owned_resources: set[str] = set()
    allocated = asyncio.Event()
    original_cancellation: asyncio.CancelledError | None = None

    async def start_async() -> None:
        nonlocal original_cancellation
        owned_resources.add("owned-runtime")
        allocated.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as error:
            original_cancellation = error
            raise

    async def stop_async() -> None:
        owned_resources.remove("owned-runtime")

    client.start.side_effect = start_async
    client.stop.side_effect = stop_async
    target = GitHubCopilotTarget(model_name="gpt-5-mini")
    request_task = asyncio.create_task(_send_normalized_async(target=target, original_value="Reply exactly HELLO."))
    try:
        await asyncio.wait_for(allocated.wait(), timeout=2.0)
        request_task.cancel()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await request_task
    finally:
        await _cancel_tasks_async(request_task)

    assert exc_info.value is original_cancellation
    sdk.CopilotClient.assert_called_once()
    client.start.assert_awaited_once()
    client.get_status.assert_not_awaited()
    client.create_session.assert_not_awaited()
    client.get_session_metadata.assert_not_awaited()
    client.delete_session.assert_not_awaited()
    session.send.assert_not_awaited()
    session.send_and_wait.assert_not_awaited()
    client.stop.assert_awaited_once()
    assert owned_resources == set()
    await target.cleanup_target_async()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("github_token", "use_working_directory", "max_requests_per_minute"),
    [
        pytest.param("  dummy-github-token  ", False, None, id="token-only"),
        pytest.param(None, True, None, id="directory-only"),
        pytest.param(None, False, 30, id="throttle-only"),
        pytest.param("  dummy-github-token  ", True, 30, id="all-options"),
    ],
)
async def test_normalizer_forwards_options_without_exposing_token_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    github_token: str | None,
    use_working_directory: bool,
    max_requests_per_minute: int | None,
) -> None:
    with (
        caplog.at_level(logging.DEBUG, logger=TARGET_LOGGER),
        patch.object(asyncio, "sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        target = GitHubCopilotTarget(
            model_name="gpt-5-mini",
            github_token=github_token,
            working_directory=tmp_path if use_working_directory else None,
            max_requests_per_minute=max_requests_per_minute,
        )
        response = await _send_normalized_async(target=target, original_value="Reply exactly HELLO.")
        await target.cleanup_target_async()
    sdk.CopilotClient.assert_called_once_with(
        github_token=github_token, working_directory=str(tmp_path) if use_working_directory else None
    )
    assert response.get_piece().converted_value == "HELLO"
    client.create_session.return_value.send_and_wait.assert_awaited_once()
    if max_requests_per_minute is not None:
        mock_sleep.assert_awaited_once_with(2.0)
        assert target.get_identifier().params["max_requests_per_minute"] == 30
    else:
        mock_sleep.assert_not_awaited()
    if use_working_directory:
        assert target.get_identifier().params["working_directory"] == str(tmp_path)
    assert "dummy-github-token" not in target.get_identifier().model_dump_json()
    assert "dummy-github-token" not in caplog.text


def test_init_without_copilot_sdk_reports_installation_guidance() -> None:
    with patch.dict("sys.modules", {"copilot": None}):
        with pytest.raises(RuntimeError, match=r"pip install pyrit\[github-copilot\]"):
            GitHubCopilotTarget(model_name="gpt-5-mini")


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        pytest.param({"model_name": "   "}, "model_name", id="blank-model"),
        pytest.param({"github_token": "   "}, "github_token", id="blank-token"),
        pytest.param({"response_timeout_seconds": 0}, "response_timeout_seconds", id="zero-timeout"),
        pytest.param({"response_timeout_seconds": -1}, "response_timeout_seconds", id="negative-timeout"),
        pytest.param({"response_timeout_seconds": float("inf")}, "response_timeout_seconds", id="infinite-timeout"),
        pytest.param({"response_timeout_seconds": float("nan")}, "response_timeout_seconds", id="nan-timeout"),
        pytest.param({"working_directory": "   "}, "working_directory", id="blank-directory"),
    ],
)
def test_init_rejects_invalid_options_before_sdk_import(*, overrides: dict[str, Any], field: str) -> None:
    with patch.dict("sys.modules", {"copilot": None}), pytest.raises(ValueError, match=field):
        GitHubCopilotTarget(**{"model_name": "gpt-5-mini", **overrides})


@pytest.mark.parametrize("path_kind", ["missing", "file"])
def test_init_rejects_non_directory_before_sdk_import(*, tmp_path: Path, path_kind: str) -> None:
    path = tmp_path / "not-a-directory"
    if path_kind == "file":
        path.write_text("local test fixture", encoding="utf-8")
    with patch.dict("sys.modules", {"copilot": None}), pytest.raises(ValueError, match="working_directory"):
        GitHubCopilotTarget(model_name="gpt-5-mini", working_directory=path)


@pytest.mark.usefixtures("patch_central_database")
async def test_normalizer_keeps_event_loop_responsive_during_client_construction_async(
    *, sdk: Any, client: NonCallableMagicMock
) -> None:
    loop = asyncio.get_running_loop()
    constructor_entered = asyncio.Event()
    constructor_finished = asyncio.Event()
    release = threading.Event()
    released_while_constructing = False

    def construct_client(*, github_token: str | None, working_directory: str | None) -> NonCallableMagicMock:
        nonlocal released_while_constructing
        try:
            loop.call_soon_threadsafe(constructor_entered.set)
            # The bound lets a blocked event loop escape without satisfying the responsiveness assertion.
            released_while_constructing = release.wait(timeout=5.0)
            return client
        finally:
            loop.call_soon_threadsafe(constructor_finished.set)

    async def release_constructor_async() -> None:
        await constructor_entered.wait()
        release.set()

    sdk.CopilotClient.side_effect = construct_client
    conversation_id = str(uuid4())
    target = GitHubCopilotTarget(model_name="gpt-5-mini")
    request_task = asyncio.create_task(
        _send_normalized_async(
            target=target,
            original_value="Reply exactly HELLO.",
            conversation_id=conversation_id,
        )
    )
    release_task = asyncio.create_task(release_constructor_async())
    try:
        await asyncio.wait_for(asyncio.gather(request_task, release_task), timeout=10.0)
    finally:
        release.set()
        await _cancel_tasks_async(request_task, release_task)
        await asyncio.wait_for(constructor_finished.wait(), timeout=5.0)

    await target.cleanup_target_async()
    client.stop.assert_awaited_once()
    client.delete_session.assert_awaited_once_with("sdk-session-id")
    assert released_while_constructing is True
