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
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, NonCallableMagicMock, call, create_autospec, patch
from uuid import UUID, uuid4

import pytest
from unit.mocks import store_message_async

from pyrit.models import Message, MessagePiece, MessageScorable, ScoringExpectation
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import (
    CapabilityHandlingPolicy,
    CapabilityName,
    GitHubCopilotTarget,
    TargetCapabilities,
    TargetConfiguration,
    UnsupportedCapabilityBehavior,
)
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
    with (
        patch.object(sdk, "CopilotClient", return_value=client),
        patch.dict("os.environ", {"GITHUB_TOKEN": ""}),
    ):
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


async def _message_state_async(
    *, memory: MemoryInterface, conversation_id: str
) -> list[tuple[str, str, str, str | None, str]]:
    return [
        (
            message.get_piece().role,
            message.get_piece().original_value,
            message.get_piece().converted_value,
            message.get_piece().conversation_id,
            message.get_piece().response_error,
        )
        for message in await memory.get_conversation_messages_async(conversation_id=conversation_id)
    ]


async def _message_roles_and_errors_async(*, memory: MemoryInterface, conversation_id: str) -> list[tuple[str, str]]:
    return [
        (message.get_piece().role, message.get_piece().response_error)
        for message in await memory.get_conversation_messages_async(conversation_id=conversation_id)
    ]


async def _message_values_and_errors_async(
    *, memory: MemoryInterface, conversation_id: str
) -> list[tuple[str, str, str]]:
    return [
        (message.get_piece().role, message.get_piece().converted_value, message.get_piece().response_error)
        for message in await memory.get_conversation_messages_async(conversation_id=conversation_id)
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
    assert await _message_state_async(memory=sqlite_instance, conversation_id=conversation_id) == [
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
        await target.set_system_prompt_async(system_prompt=initial_system_prompt, conversation_id=conversation_id)

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
            await target.set_system_prompt_async(
                system_prompt="different system instructions", conversation_id=conversation_id
            )

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
    assert await _message_state_async(memory=sqlite_instance, conversation_id=conversation_id) == expected_messages
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
        conversation_id: await _message_values_and_errors_async(memory=sqlite_instance, conversation_id=conversation_id)
        for conversation_id in ("conversation-a", "conversation-b")
    }

    await target.reset_conversation_async(conversation_id="conversation-a")
    assert session_a.disconnect.await_count == 1
    _assert_no_resource_release(client=client)
    session_b.disconnect.assert_not_awaited()
    memory_after_reset = {
        conversation_id: await _message_values_and_errors_async(memory=sqlite_instance, conversation_id=conversation_id)
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
@pytest.mark.parametrize("cancel_release", [False, True], ids=["rpc-error", "caller-cancellation"])
async def test_retained_reset_does_not_confirm_failed_sdk_disconnect_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
    caplog: pytest.LogCaptureFixture,
    cancel_release: bool,
) -> None:
    release_started = asyncio.Event()
    finish_release = asyncio.Event()
    release_error = RuntimeError("synthetic destroy RPC failure")
    release_cancellation: asyncio.CancelledError | None = None

    async def request_async(method: str, params: dict[str, Any]) -> None:
        nonlocal release_cancellation
        release_started.set()
        try:
            await finish_release.wait()
        except asyncio.CancelledError as error:
            release_cancellation = error
            raise
        raise release_error

    connection = SimpleNamespace(request=AsyncMock(side_effect=request_async))
    session_a = sdk.CopilotSession(session_id="retained-native", client=connection)
    session_b = _make_sdk_session(sdk=sdk, session_id="unrelated-native")
    session_b.send_and_wait.return_value = _assistant_reply("B")
    client.create_session.side_effect = [session_a, session_b]
    target = GitHubCopilotTarget(model_name="gpt-5-mini", retain_session=True)
    with (
        patch.object(session_a, "send_and_wait", new=AsyncMock(return_value=_assistant_reply("A"))),
        patch.object(session_a, "disconnect", wraps=session_a.disconnect) as disconnect,
    ):
        for conversation_id in ("conversation-a", "conversation-b"):
            await target.send_prompt_async(
                message=_user_message(conversation_id=conversation_id, original_value="hello")
            )
        reset_task = asyncio.create_task(target.reset_conversation_async(conversation_id="conversation-a"))
        try:
            await asyncio.wait_for(release_started.wait(), timeout=2.0)
            first_failure: BaseException
            if cancel_release:
                reset_task.cancel("cancel selected reset")
                with pytest.raises(asyncio.CancelledError) as cancellation:
                    await asyncio.wait_for(reset_task, timeout=2.0)
                assert reset_task.cancelled()
                assert cancellation.value is release_cancellation
                assert cancellation.value.args == ("cancel selected reset",)
                first_failure = cancellation.value
            else:
                finish_release.set()
                with pytest.raises(RuntimeError) as first_reset:
                    await asyncio.wait_for(reset_task, timeout=2.0)
                assert first_reset.value is release_error
                first_failure = first_reset.value

            with caplog.at_level(logging.WARNING, logger=TARGET_LOGGER):
                for _ in range(2):
                    await target.reset_conversation_async(conversation_id="conversation-a")
                    assert target._conversations["conversation-a"].session is session_a
            disconnect.assert_awaited_once()
            connection.request.assert_awaited_once()
            assert connection.request.await_args.args[1] == {"sessionId": "retained-native"}
            with pytest.raises(RuntimeError, match="retired"):
                await target.send_prompt_async(
                    message=_user_message(conversation_id="conversation-a", original_value="cannot reopen")
                )
            response = await target.send_prompt_async(
                message=_user_message(conversation_id="conversation-b", original_value="still usable")
            )
            assert response[0].get_piece().converted_value == "B"
            assert session_b.send_and_wait.await_count == 2
            session_b.disconnect.assert_not_awaited()
            _assert_no_resource_release(client=client)

            with caplog.at_level(logging.WARNING, logger=TARGET_LOGGER):
                await target.cleanup_target_async()
                await target.reset_conversation_async(conversation_id="conversation-a")
            unconfirmed_warnings = [
                r for r in caplog.records if r.name == TARGET_LOGGER and "unconfirmed" in r.getMessage()
            ]
            assert len(unconfirmed_warnings) == 3
            assert all(r.exc_info is not None and r.exc_info[1] is first_failure for r in unconfirmed_warnings)
            await target.reset_conversation_async(conversation_id="conversation-b")
            disconnect.assert_awaited_once()
            connection.request.assert_awaited_once()
            session_b.disconnect.assert_awaited_once()
            client.stop.assert_awaited_once()
            client.delete_session.assert_not_awaited()
            assert target._conversations["conversation-a"].session is session_a
            assert client.create_session.await_count == 2
        finally:
            finish_release.set()
            await _cancel_tasks_async(reset_task)
            with suppress(Exception):
                await asyncio.wait_for(target.cleanup_target_async(), timeout=2.0)


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
    ("failed_conversation", "failure_kind"),
    [
        pytest.param("conversation-a", "error", id="selected-session-release-fails"),
        pytest.param("conversation-b", "error", id="unrelated-session-release-fails"),
        pytest.param("conversation-a", "release-cancel", id="selected-session-release-cancelled"),
        pytest.param("conversation-b", "release-cancel", id="unrelated-session-release-cancelled"),
        pytest.param(None, "caller-cancel", id="reset-caller-cancelled"),
        pytest.param(
            "conversation-a",
            "release-cancel-stop-error",
            id="selected-release-cancelled-stop-fails",
        ),
        pytest.param(
            "conversation-b",
            "release-cancel-stop-error",
            id="unrelated-release-cancelled-stop-fails",
        ),
    ],
)
async def test_reset_during_cleanup_propagates_only_selected_session_failure_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
    failed_conversation: str | None,
    failure_kind: str,
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
    selected_cancellation = asyncio.CancelledError("selected session A release cancelled")
    unrelated_cancellation = asyncio.CancelledError("unrelated session B release cancelled")
    stop_failure = RuntimeError("client stop failed")
    if failure_kind == "release-cancel-stop-error":
        client.stop.side_effect = stop_failure

    async def disconnect_a_async() -> None:
        a_release_started.set()
        try:
            await release_a.wait()
            if failed_conversation == "conversation-a":
                if failure_kind == "error":
                    raise a_failure
                if failure_kind in ("release-cancel", "release-cancel-stop-error"):
                    raise selected_cancellation
        finally:
            a_release_finished.set()

    async def disconnect_b_async() -> None:
        b_release_started.set()
        await release_b.wait()
        if failed_conversation == "conversation-b":
            if failure_kind == "error":
                raise b_failure
            if failure_kind in ("release-cancel", "release-cancel-stop-error"):
                raise unrelated_cancellation

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

        if failure_kind == "caller-cancel":
            reset_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(reset_task, timeout=2.0)
            assert reset_task.cancelled()
            assert not cleanup_task.done()
            client.stop.assert_not_awaited()
            release_b.set()
            await asyncio.wait_for(cleanup_task, timeout=2.0)
        else:
            release_b.set()
            if failure_kind == "error":
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
            elif failure_kind == "release-cancel-stop-error":
                with pytest.raises(BaseExceptionGroup) as cleanup_error:
                    await asyncio.wait_for(cleanup_task, timeout=2.0)
                expected_release_failure = (
                    selected_cancellation if failed_conversation == "conversation-a" else unrelated_cancellation
                )
                assert len(cleanup_error.value.exceptions) == 2
                assert cleanup_error.value.exceptions[0] is expected_release_failure
                assert cleanup_error.value.exceptions[1] is stop_failure
                if failed_conversation == "conversation-a":
                    with pytest.raises(BaseExceptionGroup) as reset_error:
                        await asyncio.wait_for(reset_task, timeout=2.0)
                    assert reset_error.value is cleanup_error.value
                else:
                    await asyncio.wait_for(reset_task, timeout=2.0)
            else:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(cleanup_task, timeout=2.0)
                if failed_conversation == "conversation-a":
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(reset_task, timeout=2.0)
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
    assert "retired after a failed send: TimeoutError('ambiguous mock send')" in str(retired_error.value.__cause__)

    conversation_b = "fresh-conversation"
    response_b = await _send_normalized_async(
        target=target,
        original_value="fresh original",
        converted_value="fresh prepared",
        conversation_id=conversation_b,
    )
    assert response_b.get_piece().converted_value == "FRESH"
    assert await _message_roles_and_errors_async(memory=sqlite_instance, conversation_id=conversation_a) == [
        ("user", "none"),
        ("assistant", "processing"),
        ("user", "none"),
        ("assistant", "processing"),
    ]
    assert await _message_values_and_errors_async(memory=sqlite_instance, conversation_id=conversation_b) == [
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
@pytest.mark.parametrize(
    "initial_reply",
    [
        pytest.param("malformed", id="malformed-json"),
        pytest.param("empty", id="empty-root"),
        pytest.param("absent", id="absent-root"),
    ],
)
async def test_self_ask_true_false_uses_fresh_copilot_session_after_unusable_reply_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
    initial_reply: str,
) -> None:
    failed_session = _make_sdk_session(sdk=sdk, session_id="malformed-judge-session")
    failed_session.send_and_wait.return_value = {
        "malformed": _assistant_reply("malformed judge response"),
        "empty": _assistant_reply(""),
        "absent": None,
    }[initial_reply]
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
    saved_answer = await store_message_async(
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
@pytest.mark.parametrize(
    "release_fails",
    [pytest.param(False, id="release-success"), pytest.param(True, id="release-failure")],
)
async def test_cancelled_send_keeps_retirement_owned_until_cleanup_async(
    *,
    client: NonCallableMagicMock,
    release_fails: bool,
) -> None:
    session = client.create_session.return_value
    session.send_and_wait.side_effect = TimeoutError("ambiguous mock send")
    release_error = RuntimeError("Synthetic session release failure")
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
        if delete_count == 1 and release_fails:
            raise release_error

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
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await asyncio.wait_for(send_task, timeout=2.0)
        if release_fails:
            assert exc_info.value.__cause__ is release_error
        await asyncio.wait_for(cleanup_task, timeout=2.0)
        expected_delete_calls = (
            [call(session.session_id), call(session.session_id)] if release_fails else [call(session.session_id)]
        )
        assert client.delete_session.await_args_list == expected_delete_calls
        client.stop.assert_awaited_once()
        assert all(task.done() for task in delete_tasks)
    finally:
        release_delete.set()
        tasks = {send_task, *delete_tasks}
        if cleanup_task is not None:
            tasks.add(cleanup_task)
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=2.0)


@pytest.mark.usefixtures("patch_central_database")
async def test_cancelled_send_preserves_cancellation_when_retirement_fails_async(
    *,
    client: NonCallableMagicMock,
) -> None:
    session = client.create_session.return_value
    send_started = asyncio.Event()
    wait_for_cancel = asyncio.Event()
    release_error = RuntimeError("Synthetic session release failure")

    async def send_and_wait_async(*_args: Any, **_kwargs: Any) -> None:
        send_started.set()
        await wait_for_cancel.wait()

    session.send_and_wait.side_effect = send_and_wait_async
    client.delete_session.side_effect = [release_error, None]
    target = GitHubCopilotTarget(model_name="gpt-5-mini")
    send_task = asyncio.create_task(
        target.send_prompt_async(
            message=_user_message(
                conversation_id="cancelled-send-release-failure",
                original_value="cancelled request",
            )
        )
    )

    try:
        await asyncio.wait_for(send_started.wait(), timeout=2.0)
        send_task.cancel()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await asyncio.wait_for(send_task, timeout=2.0)
        assert exc_info.value.__cause__ is release_error
        client.delete_session.assert_awaited_once_with(session.session_id)
    finally:
        await _cancel_tasks_async(send_task)
        await target.cleanup_target_async()

    assert client.delete_session.await_args_list == [
        call(session.session_id),
        call(session.session_id),
    ]
    client.stop.assert_awaited_once()


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


@pytest.mark.usefixtures("patch_central_database", "sdk")
@pytest.mark.parametrize(
    "configuration",
    [
        pytest.param(
            TargetConfiguration(
                capabilities=TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True),
                policy=CapabilityHandlingPolicy(
                    behaviors={
                        CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
                        CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
                        CapabilityName.JSON_SCHEMA: UnsupportedCapabilityBehavior.RAISE,
                    }
                ),
            ),
            id="policy-override",
        ),
        pytest.param(
            TargetConfiguration(capabilities=TargetCapabilities(supports_multi_turn=True)),
            id="narrowed",
        ),
    ],
)
def test_target_accepts_custom_configuration_within_native_capabilities(configuration: TargetConfiguration) -> None:
    target = GitHubCopilotTarget(model_name="gpt-5-mini", custom_configuration=configuration)

    assert target.configuration is configuration


@pytest.mark.parametrize(
    ("capabilities", "unsupported"),
    [
        pytest.param(
            TargetCapabilities(
                supports_multi_turn=True, supports_system_prompt=True, supports_multi_message_pieces=True
            ),
            "supports_multi_message_pieces",
            id="multi-piece",
        ),
        pytest.param(
            TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True, supports_editable_history=True),
            "supports_editable_history",
            id="editable-history",
        ),
        pytest.param(
            TargetCapabilities(
                supports_multi_turn=True,
                supports_system_prompt=True,
                input_modalities=frozenset({frozenset({"text"}), frozenset({"image_path"})}),
            ),
            "input_modalities",
            id="image-input",
        ),
    ],
)
def test_target_rejects_custom_configuration_with_unimplemented_capability(
    *,
    capabilities: TargetCapabilities,
    unsupported: str,
) -> None:
    with pytest.raises(ValueError, match=rf"does not implement: {unsupported}\.$"):
        GitHubCopilotTarget(
            model_name="gpt-5-mini",
            custom_configuration=TargetConfiguration(capabilities=capabilities),
        )


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
@pytest.mark.parametrize(
    "conversation_id",
    [pytest.param(None, id="missing"), pytest.param("", id="empty")],
)
async def test_direct_send_requires_nonempty_conversation_id_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
    conversation_id: str | None,
) -> None:
    target = GitHubCopilotTarget(model_name="gpt-5-mini")

    with pytest.raises(ValueError, match="conversation_id"):
        await target.send_prompt_async(
            message=_user_message(
                conversation_id=conversation_id,
                original_value="direct request",
            )
        )

    sdk.CopilotClient.assert_not_called()
    client.start.assert_not_awaited()
    client.create_session.assert_not_awaited()


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
        assert await _message_roles_and_errors_async(memory=sqlite_instance, conversation_id=conversation_id) == [
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
    assert await _message_roles_and_errors_async(memory=sqlite_instance, conversation_id=conversation_id) == [
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
@pytest.mark.parametrize(
    "invalid_reply",
    ["subagent", "empty-subagent", "non-assistant"],
)
async def test_normalizer_rejects_invalid_reply_async(
    *,
    client: NonCallableMagicMock,
    sqlite_instance: MemoryInterface,
    invalid_reply: str,
) -> None:
    from copilot.generated.session_events import SessionEventType, SessionIdleData

    reply = _assistant_reply("HELLO")
    empty_subagent_reply = replace(_assistant_reply(""), agent_id="sdk-subagent-id")
    session = client.create_session.return_value
    session.send_and_wait.return_value = {
        "subagent": replace(reply, agent_id="sdk-subagent-id"),
        "empty-subagent": empty_subagent_reply,
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

    assert await _message_roles_and_errors_async(memory=sqlite_instance, conversation_id=conversation_id) == [
        ("user", "none"),
        ("assistant", "processing"),
    ]
    session.send_and_wait.assert_awaited_once()
    session.on.return_value.assert_called_once_with()
    client.delete_session.assert_awaited_once_with("sdk-session-id")
    with pytest.raises(Exception, match="Error sending prompt with conversation ID:") as retired_error:
        await _send_normalized_async(
            target=target,
            original_value="Reply exactly HELLO again.",
            conversation_id=conversation_id,
        )
    assert "retired after a failed send: ValueError(" in str(retired_error.value.__cause__)
    session.send_and_wait.assert_awaited_once()
    client.stop.assert_not_awaited()
    await target.cleanup_target_async()
    client.stop.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("empty_reply", ["empty", "absent"])
async def test_empty_reply_keeps_native_conversation_async(
    *,
    client: NonCallableMagicMock,
    sqlite_instance: MemoryInterface,
    empty_reply: str,
) -> None:
    session = client.create_session.return_value
    session.send_and_wait.side_effect = [
        {"empty": _assistant_reply(""), "absent": None}[empty_reply],
        _assistant_reply("HELLO"),
    ]
    conversation_id = str(uuid4())
    target = GitHubCopilotTarget(model_name="gpt-5-mini")

    empty_response = await _send_normalized_async(
        target=target,
        original_value="Reply exactly HELLO.",
        conversation_id=conversation_id,
    )
    response = await _send_normalized_async(
        target=target,
        original_value="Reply exactly HELLO again.",
        conversation_id=conversation_id,
    )

    assert empty_response.get_piece().converted_value == ""
    assert response.get_piece().converted_value == "HELLO"
    assert await _message_roles_and_errors_async(memory=sqlite_instance, conversation_id=conversation_id) == [
        ("user", "none"),
        ("assistant", "empty"),
        ("user", "none"),
        ("assistant", "none"),
    ]
    client.create_session.assert_awaited_once()
    client.delete_session.assert_not_awaited()
    await target.cleanup_target_async()
    client.delete_session.assert_awaited_once_with("sdk-session-id")
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
    assert await _message_roles_and_errors_async(memory=sqlite_instance, conversation_id=conversation_id) == [
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
@pytest.mark.parametrize(
    ("retain_session", "retry_surface"),
    [
        pytest.param(False, None, id="delete"),
        pytest.param(True, None, id="retain"),
        pytest.param(False, "terminal-cleanup", id="terminal-cleanup-retry"),
        pytest.param(False, "selected-reset", id="selected-reset-retry"),
        pytest.param(True, "selected-reset", id="retain-selected-reset-retry"),
    ],
)
async def test_normalizer_cleans_up_partial_creation_async(
    *,
    client: NonCallableMagicMock,
    sqlite_instance: MemoryInterface,
    caplog: pytest.LogCaptureFixture,
    retain_session: bool,
    retry_surface: str | None,
) -> None:
    from copilot.generated.rpc import SessionsCloseRequest

    session = client.create_session.return_value
    sessions = {"unrelated-session-id"}
    attached: set[str] = set()
    creation_error = RuntimeError("Copilot post-create options update failed")
    release_error = RuntimeError("Copilot partial-session release failed")

    async def create_session_async(*, session_id: str = "sdk-generated-session-id", **_kwargs: Any) -> None:
        sessions.add(session_id)
        attached.add(session_id)
        raise creation_error

    async def delete_session_async(session_id: str) -> None:
        if retry_surface is not None and client.delete_session.await_count == 1:
            raise release_error
        sessions.remove(session_id)
        attached.discard(session_id)

    async def close_session_async(request: SessionsCloseRequest) -> None:
        if retry_surface is not None and client.rpc.sessions.close.await_count == 1:
            raise release_error
        attached.discard(request.session_id)

    client.create_session.side_effect = create_session_async
    _mock_session_storage(client=client, sessions=sessions)
    client.delete_session.side_effect = delete_session_async
    client.rpc.sessions.close = AsyncMock(side_effect=close_session_async)
    release = client.rpc.sessions.close if retain_session else client.delete_session
    conversation_id = str(uuid4())
    target = GitHubCopilotTarget(model_name="gpt-5-mini", retain_session=retain_session)
    with caplog.at_level(logging.INFO, logger=TARGET_LOGGER):
        with pytest.raises(Exception, match="Error sending prompt with conversation ID:") as exc_info:
            await _send_normalized_async(
                target=target,
                original_value="Reply exactly HELLO.",
                conversation_id=conversation_id,
            )

    allocated_session_id = client.create_session.await_args.kwargs["session_id"]
    release_call = (
        call(SessionsCloseRequest(session_id=allocated_session_id)) if retain_session else call(allocated_session_id)
    )
    assert exc_info.value.__cause__ is (release_error if retry_surface is not None else creation_error)
    if retry_surface is not None:
        assert release_error.__context__ is creation_error
    assert str(UUID(allocated_session_id)) == allocated_session_id
    assert allocated_session_id != "sdk-session-id"
    client.create_session.assert_awaited_once()
    client.get_session_metadata.assert_awaited_once_with(allocated_session_id)
    session.send_and_wait.assert_not_awaited()
    session.send.assert_not_awaited()
    client.stop.assert_not_awaited()
    assert await _message_roles_and_errors_async(memory=sqlite_instance, conversation_id=conversation_id) == [
        ("user", "none"),
        ("assistant", "processing"),
    ]
    if retry_surface is not None:
        assert sessions == {"unrelated-session-id", allocated_session_id}
        assert attached == {allocated_session_id}
        assert release.await_args_list == [release_call]
        with pytest.raises(Exception, match="Error sending prompt with conversation ID:") as retry_error:
            await _send_normalized_async(
                target=target,
                original_value="Retry after failed partial cleanup.",
                conversation_id=conversation_id,
            )
        assert isinstance(retry_error.value.__cause__, RuntimeError)
        assert "retired" in str(retry_error.value.__cause__).lower()
        client.create_session.assert_awaited_once()
        client.get_session_metadata.assert_awaited_once_with(allocated_session_id)
        assert release.await_args_list == [release_call]
        session.send_and_wait.assert_not_awaited()
        session.send.assert_not_awaited()
        assert await _message_roles_and_errors_async(memory=sqlite_instance, conversation_id=conversation_id) == [
            ("user", "none"),
            ("assistant", "processing"),
            ("user", "none"),
            ("assistant", "processing"),
        ]

    try:
        if retry_surface == "selected-reset":
            with caplog.at_level(logging.INFO, logger=TARGET_LOGGER):
                await target.reset_conversation_async(conversation_id=conversation_id)
            assert release.await_args_list == [release_call, release_call]
            assert client.get_session_metadata.await_args_list == [
                call(allocated_session_id),
                call(allocated_session_id),
            ]
            assert attached == set()
            assert sessions == (
                {"unrelated-session-id", allocated_session_id} if retain_session else {"unrelated-session-id"}
            )
            client.stop.assert_not_awaited()
    finally:
        await target.cleanup_target_async()

    client.stop.assert_awaited_once()
    if retry_surface is not None:
        assert client.get_session_metadata.await_args_list == [
            call(allocated_session_id),
            call(allocated_session_id),
        ]
    assert sessions == ({"unrelated-session-id", allocated_session_id} if retain_session else {"unrelated-session-id"})
    retained_logs = [
        r.getMessage()
        for r in caplog.records
        if r.name == TARGET_LOGGER
        and r.levelno == logging.INFO
        and r.getMessage().startswith("Retaining Copilot session ")
    ]
    assert release.await_args_list == ([release_call, release_call] if retry_surface is not None else [release_call])
    assert attached == set()
    if retain_session:
        client.delete_session.assert_not_awaited()
        assert sessions == {"unrelated-session-id", allocated_session_id}
        assert retained_logs == [f"Retaining Copilot session {allocated_session_id} as requested; delete it manually."]
    else:
        client.rpc.sessions.close.assert_not_awaited()
        assert sessions == {"unrelated-session-id"}
        assert not retained_logs


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "failure_stage",
    [
        pytest.param("metadata", id="metadata-uncertainty"),
        pytest.param("delete", id="persistent-delete-failure"),
    ],
)
async def test_normalizer_reports_partial_creation_cleanup_retry_failure_async(
    *,
    client: NonCallableMagicMock,
    sqlite_instance: MemoryInterface,
    failure_stage: str,
) -> None:
    sessions = {"unrelated-session-id"}
    creation_error = RuntimeError("Copilot post-create options update failed")
    initial_cleanup_error = RuntimeError("Initial partial-session cleanup failed")
    terminal_cleanup_error = RuntimeError("Terminal partial-session cleanup failed")

    async def create_session_async(*, session_id: str = "sdk-generated-session-id", **_kwargs: Any) -> None:
        sessions.add(session_id)
        raise creation_error

    client.create_session.side_effect = create_session_async
    _mock_session_storage(client=client, sessions=sessions)
    if failure_stage == "metadata":
        client.get_session_metadata.side_effect = [initial_cleanup_error, terminal_cleanup_error]
    else:
        client.delete_session.side_effect = [initial_cleanup_error, terminal_cleanup_error]

    conversation_id = str(uuid4())
    target = GitHubCopilotTarget(model_name="gpt-5-mini")
    with pytest.raises(Exception, match="Error sending prompt with conversation ID:") as send_error:
        await _send_normalized_async(
            target=target,
            original_value="Reply exactly HELLO.",
            conversation_id=conversation_id,
        )

    allocated_session_id = client.create_session.await_args.kwargs["session_id"]
    assert send_error.value.__cause__ is initial_cleanup_error
    assert initial_cleanup_error.__context__ is creation_error
    assert str(UUID(allocated_session_id)) == allocated_session_id
    client.create_session.assert_awaited_once()
    client.get_session_metadata.assert_awaited_once_with(allocated_session_id)
    session = client.create_session.return_value
    session.send_and_wait.assert_not_awaited()
    session.send.assert_not_awaited()
    assert await _message_roles_and_errors_async(memory=sqlite_instance, conversation_id=conversation_id) == [
        ("user", "none"),
        ("assistant", "processing"),
    ]
    assert sessions == {"unrelated-session-id", allocated_session_id}
    client.stop.assert_not_awaited()

    with pytest.raises(RuntimeError) as cleanup_error:
        await target.cleanup_target_async()

    assert cleanup_error.value is terminal_cleanup_error
    assert client.get_session_metadata.await_args_list == [
        call(allocated_session_id),
        call(allocated_session_id),
    ]
    if failure_stage == "metadata":
        client.delete_session.assert_not_awaited()
    else:
        assert client.delete_session.await_args_list == [
            call(allocated_session_id),
            call(allocated_session_id),
        ]
    assert sessions == {"unrelated-session-id", allocated_session_id}
    client.stop.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "first_delete_failure",
    [
        pytest.param("none", id="cleanup-succeeds"),
        pytest.param("runtime-error", id="first-delete-runtime-error"),
        pytest.param("cancelled-error", id="first-delete-cancelled-error"),
    ],
)
async def test_normalizer_deletes_owned_session_when_creation_is_cancelled_after_allocation_async(
    *,
    client: NonCallableMagicMock,
    first_delete_failure: str,
) -> None:
    session = client.create_session.return_value
    sessions = {"unrelated-session-id"}
    allocated = asyncio.Event()
    conversation_id = str(uuid4())
    cancellation_message = "cancel caller after session allocation"
    caller_cancellation: asyncio.CancelledError | None = None
    cleanup_failure: RuntimeError | asyncio.CancelledError | None = None
    if first_delete_failure == "runtime-error":
        cleanup_failure = RuntimeError("first partial-session deletion failed")
    elif first_delete_failure == "cancelled-error":
        cleanup_failure = asyncio.CancelledError("cleanup-origin cancellation")

    async def create_session_async(*, session_id: str = "sdk-generated-session-id", **_kwargs: Any) -> None:
        nonlocal caller_cancellation
        sessions.add(session_id)
        allocated.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as error:
            caller_cancellation = error
            raise

    async def delete_session_async(session_id: str) -> None:
        if client.delete_session.await_count == 1 and cleanup_failure is not None:
            raise cleanup_failure
        sessions.remove(session_id)

    client.create_session.side_effect = create_session_async
    _mock_session_storage(client=client, sessions=sessions)
    client.delete_session.side_effect = delete_session_async
    target = GitHubCopilotTarget(model_name="gpt-5-mini")
    request_task = asyncio.create_task(
        _send_normalized_async(
            target=target,
            original_value="Reply exactly HELLO.",
            conversation_id=conversation_id,
        )
    )
    try:
        await asyncio.wait_for(allocated.wait(), timeout=2.0)
        request_task.cancel(cancellation_message)
        with pytest.raises(asyncio.CancelledError) as cancellation_error:
            await asyncio.wait_for(request_task, timeout=2.0)
    finally:
        await _cancel_tasks_async(request_task)

    assert request_task.done()
    assert request_task.cancelled()
    assert caller_cancellation is not None
    assert cancellation_error.value is caller_cancellation
    assert cancellation_error.value.args == (cancellation_message,)
    assert cancellation_error.value.__cause__ is cleanup_failure

    client.create_session.assert_awaited_once()
    allocated_id = client.create_session.await_args.kwargs["session_id"]
    client.get_session_metadata.assert_awaited_once_with(allocated_id)
    client.delete_session.assert_awaited_once_with(allocated_id)
    session.send_and_wait.assert_not_awaited()
    session.send.assert_not_awaited()
    client.stop.assert_not_awaited()
    if first_delete_failure == "none":
        assert sessions == {"unrelated-session-id"}
    else:
        assert sessions == {"unrelated-session-id", allocated_id}
        await target.reset_conversation_async(conversation_id=conversation_id)
        assert client.get_session_metadata.await_args_list == [
            call(allocated_id),
            call(allocated_id),
        ]
        assert client.delete_session.await_args_list == [call(allocated_id), call(allocated_id)]
        assert sessions == {"unrelated-session-id"}
        client.stop.assert_not_awaited()

    await target.cleanup_target_async()
    client.stop.assert_awaited_once()
    assert sessions == {"unrelated-session-id"}


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("failure_stage", ["start", "status"])
@pytest.mark.parametrize("stop_failure", ["caller-cancel", "runtime-error", "sdk-cancel"])
async def test_failed_startup_stop_preserves_cancellation_and_ownership_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
    failure_stage: str,
    stop_failure: str,
) -> None:
    from copilot.client import CopilotClient, GetStatusResponse

    startup_error = RuntimeError("synthetic startup failure")
    cleanup_error = (
        asyncio.CancelledError("SDK stop cancellation")
        if stop_failure == "sdk-cancel"
        else RuntimeError("synthetic stop failure")
    )
    caller_cancellation: asyncio.CancelledError | None = None
    stop_started = asyncio.Event()
    finish_stop = asyncio.Event()
    owned_resources = {"failed-startup-runtime"}

    async def stop_async() -> None:
        nonlocal caller_cancellation
        if client.stop.await_count == 1:
            stop_started.set()
            try:
                await finish_stop.wait()
            except asyncio.CancelledError as error:
                caller_cancellation = error
                raise
            raise cleanup_error
        owned_resources.clear()

    (client.start if failure_stage == "start" else client.get_status).side_effect = startup_error
    client.stop.side_effect = stop_async
    target = GitHubCopilotTarget(model_name="gpt-5-mini")
    send_task = asyncio.create_task(
        target.send_prompt_async(message=_user_message(conversation_id="failed-startup", original_value="hello"))
    )
    try:
        await asyncio.wait_for(stop_started.wait(), timeout=2.0)
        if stop_failure == "caller-cancel":
            send_task.cancel("caller cancelled during startup cleanup")
            with pytest.raises(asyncio.CancelledError) as cancellation:
                await asyncio.wait_for(send_task, timeout=2.0)
            assert send_task.cancelled()
            assert cancellation.value is caller_cancellation
            assert cancellation.value.args == ("caller cancelled during startup cleanup",)
            assert cancellation.value.__cause__ is startup_error
        else:
            finish_stop.set()
            with pytest.raises(BaseExceptionGroup) as failure:
                await asyncio.wait_for(send_task, timeout=2.0)
            assert failure.value.exceptions == (startup_error, cleanup_error)
            assert failure.value.__cause__ is startup_error
            assert not send_task.cancelled()

        assert owned_resources == {"failed-startup-runtime"}
        assert target._client is None
        client.create_session.assert_not_awaited()
        client.stop.assert_awaited_once()

        fresh_client = create_autospec(CopilotClient, instance=True)
        fresh_client.get_status.return_value = GetStatusResponse(version="offline", protocol_version=3)
        fresh_session = _make_sdk_session(sdk=sdk, session_id="fresh-native")
        fresh_session.send_and_wait.return_value = _assistant_reply("FRESH")
        fresh_client.create_session.return_value = fresh_session
        sdk.CopilotClient.return_value = fresh_client
        response = await target.send_prompt_async(
            message=_user_message(conversation_id="fresh-startup", original_value="new client")
        )
        assert response[0].get_piece().converted_value == "FRESH"
        assert target._client is fresh_client
        client.create_session.assert_not_awaited()
        fresh_client.start.assert_awaited_once()
        fresh_client.stop.assert_not_awaited()

        await target.cleanup_target_async()
        await target.cleanup_target_async()
        assert client.stop.await_count == 2
        assert not owned_resources
        fresh_client.delete_session.assert_awaited_once_with("fresh-native")
        fresh_client.stop.assert_awaited_once()
    finally:
        finish_stop.set()
        await _cancel_tasks_async(send_task)
        with suppress(Exception):
            await asyncio.wait_for(target.cleanup_target_async(), timeout=2.0)


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
    ("github_token", "environment_token", "expected_github_token", "use_working_directory", "max_requests_per_minute"),
    [
        pytest.param("  dummy-github-token  ", None, "  dummy-github-token  ", False, None, id="token-only"),
        pytest.param(None, None, None, True, None, id="directory-only"),
        pytest.param(None, "", None, False, 30, id="throttle-only"),
        pytest.param("  dummy-github-token  ", None, "  dummy-github-token  ", True, 30, id="all-options"),
        pytest.param(
            "  dummy-github-token  ",
            "environment-github-token",
            "  dummy-github-token  ",
            False,
            None,
            id="explicit-token-precedence",
        ),
        pytest.param(
            None,
            "environment-github-token",
            "environment-github-token",
            False,
            None,
            id="environment-token",
        ),
    ],
)
async def test_normalizer_forwards_options_without_exposing_token_async(
    *,
    sdk: Any,
    client: NonCallableMagicMock,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    github_token: str | None,
    environment_token: str | None,
    expected_github_token: str | None,
    use_working_directory: bool,
    max_requests_per_minute: int | None,
) -> None:
    with (
        patch.dict(
            "os.environ",
            {} if environment_token is None else {"GITHUB_TOKEN": environment_token},
            clear=True,
        ),
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
        github_token=expected_github_token, working_directory=str(tmp_path) if use_working_directory else None
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
    for token in ("dummy-github-token", "environment-github-token"):
        assert token not in target.get_identifier().model_dump_json()
        assert token not in caplog.text


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
