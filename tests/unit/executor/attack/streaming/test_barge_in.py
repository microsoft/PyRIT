# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for ``BargeInAttack``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack import BargeInAttack, BargeInAttackContext
from pyrit.executor.attack.core import AttackParameters
from pyrit.models import AttackOutcome, Message, MessagePiece
from pyrit.prompt_target import RealtimeTarget

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_CLEAN_ENV = {"OPENAI_REALTIME_UNDERLYING_MODEL": ""}


@pytest.fixture
@patch.dict("os.environ", _CLEAN_ENV)
def vad_target(sqlite_instance):
    return RealtimeTarget(api_key="test_key", endpoint="wss://test_url", model_name="test")


async def _aiter(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for c in chunks:
        yield c


def _attack_context(*, audio_chunks: AsyncIterator[bytes], objective: str = "obj") -> BargeInAttackContext[Any]:
    return BargeInAttackContext(
        params=AttackParameters(objective=objective),
        audio_chunks=audio_chunks,
    )


def _mock_connection() -> AsyncMock:
    connection = AsyncMock()
    connection.input_audio_buffer.append = AsyncMock()
    connection.conversation.item.create = AsyncMock()
    connection.conversation.item.delete = AsyncMock()
    connection.response.create = AsyncMock()
    connection.session.update = AsyncMock()
    connection.close = AsyncMock()
    return connection


# ---- Construction validation -----------------------------------------------------------------


@patch.dict("os.environ", _CLEAN_ENV)
def test_constructor_rejects_target_without_streaming_capability(sqlite_instance):
    """A target whose capabilities lack STREAMING_AUDIO must be rejected at construction."""
    from pyrit.prompt_target import OpenAIChatTarget

    no_streaming = OpenAIChatTarget(api_key="k", endpoint="https://x", model_name="m")
    with pytest.raises(Exception, match="streaming_audio"):
        BargeInAttack(objective_target=no_streaming)


def test_constructor_succeeds_with_vad_target(vad_target):
    """A RealtimeTarget declares STREAMING_AUDIO — construction succeeds."""
    attack = BargeInAttack(objective_target=vad_target)
    assert attack.get_objective_target() is vad_target


# ---- Context validation ----------------------------------------------------------------------


async def test_validate_context_requires_objective(vad_target):
    attack = BargeInAttack(objective_target=vad_target)
    ctx = BargeInAttackContext(
        params=AttackParameters(objective=""),
        audio_chunks=_aiter([b"\x00" * 96]),
    )
    with pytest.raises(ValueError, match="objective"):
        attack._validate_context(context=ctx)


async def test_validate_context_requires_audio_chunks(vad_target):
    attack = BargeInAttack(objective_target=vad_target)
    ctx = BargeInAttackContext(
        params=AttackParameters(objective="o"),
        audio_chunks=None,
    )
    with pytest.raises(ValueError, match="audio_chunks"):
        attack._validate_context(context=ctx)


# ---- _setup_async + prepended_conversation persistence ---------------------------------------


async def test_setup_async_persists_prepended_conversation_to_memory(vad_target):
    """Prepended_conversation messages must be written to memory on setup like other attacks do."""
    attack = BargeInAttack(objective_target=vad_target)
    sys_msg = Message(
        message_pieces=[
            MessagePiece(
                role="system",
                original_value="You are a strict assistant.",
                original_value_data_type="text",
                converted_value="You are a strict assistant.",
                converted_value_data_type="text",
                conversation_id="ignored-by-setup",
            )
        ]
    )
    user_msg = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="prior user turn",
                original_value_data_type="text",
                converted_value="prior user turn",
                converted_value_data_type="text",
                conversation_id="ignored-by-setup",
            )
        ]
    )
    assistant_msg = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value="prior assistant turn",
                original_value_data_type="text",
                converted_value="prior assistant turn",
                converted_value_data_type="text",
                conversation_id="ignored-by-setup",
            )
        ]
    )

    ctx = BargeInAttackContext(
        params=AttackParameters(
            objective="o",
            prepended_conversation=[sys_msg, user_msg, assistant_msg],
        ),
        audio_chunks=_aiter([b"\x00" * 96]),
    )

    add_calls: list[Any] = []
    with patch.object(attack._conversation_manager._memory, "add_message_to_memory") as mock_add:
        mock_add.side_effect = lambda **kw: add_calls.append(kw["request"])
        await attack._setup_async(context=ctx)

    # All three prepended messages should have been written to memory under the
    # attack's conversation_id; assistant role becomes simulated_assistant on storage.
    assert len(add_calls) == 3
    storage_roles = [m.message_pieces[0].role for m in add_calls]
    assert storage_roles == ["system", "user", "simulated_assistant"]
    # All three messages share the context's conversation_id post-setup.
    for m in add_calls:
        assert m.message_pieces[0].conversation_id == ctx.conversation_id


async def test_setup_async_no_op_when_prepended_conversation_empty(vad_target):
    """Empty prepended_conversation: no memory writes, no crash."""
    attack = BargeInAttack(objective_target=vad_target)
    ctx = BargeInAttackContext(
        params=AttackParameters(objective="o"),  # no prepended_conversation
        audio_chunks=_aiter([b"\x00" * 96]),
    )

    add_calls: list[Any] = []
    with patch.object(attack._conversation_manager._memory, "add_message_to_memory") as mock_add:
        mock_add.side_effect = lambda **kw: add_calls.append(kw["request"])
        await attack._setup_async(context=ctx)

    assert add_calls == []


# ---- _perform_async: session factory passthrough ----------------------------------------------


def _assistant_message(text: str = "ok") -> Message:
    return Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=text,
                original_value_data_type="text",
                converted_value=text,
                converted_value_data_type="text",
                conversation_id="any",
            )
        ]
    )


def _fake_session(messages: list[Message] | None = None, raise_exc: Exception | None = None) -> MagicMock:
    """Return a mock session whose ``run_async()`` yields ``messages`` or raises ``raise_exc``."""

    async def _gen():
        for m in messages or []:
            yield m
        if raise_exc is not None:
            raise raise_exc

    session = MagicMock()
    session.run_async = MagicMock(return_value=_gen())
    return session


async def test_perform_async_opens_session_with_expected_kwargs(vad_target):
    """``_perform_async`` constructs the session via the target factory with the right kwargs."""
    attack = BargeInAttack(objective_target=vad_target)
    fake = _fake_session(messages=[_assistant_message()])

    chunks = _aiter([b"\x00" * 96])
    ctx = _attack_context(audio_chunks=chunks)

    with patch.object(RealtimeTarget, "open_streaming_session", return_value=fake) as factory:
        await attack._perform_async(context=ctx)

    factory.assert_called_once()
    kwargs = factory.call_args.kwargs
    assert kwargs["audio_chunks"] is chunks
    assert kwargs["prompt_normalizer"] is attack._prompt_normalizer
    assert kwargs["conversation_id"] == ctx.conversation_id
    assert kwargs["request_converter_configurations"] == attack._request_converters
    assert kwargs["response_converter_configurations"] == attack._response_converters
    assert kwargs["prepended_conversation"] == ctx.prepended_conversation
    assert kwargs["persist_prepended_conversation"] is False


async def test_perform_async_aggregates_assistant_turns(vad_target):
    """Multiple yielded Messages bump executed_turns and last_response tracks the final one."""
    attack = BargeInAttack(objective_target=vad_target)
    messages = [_assistant_message("first"), _assistant_message("second")]
    fake = _fake_session(messages=messages)

    ctx = _attack_context(audio_chunks=_aiter([b"\x00"]))

    with patch.object(RealtimeTarget, "open_streaming_session", return_value=fake):
        result = await attack._perform_async(context=ctx)

    assert result.executed_turns == 2
    assert result.last_response is not None
    assert result.last_response.converted_value == "second"
    assert result.outcome == AttackOutcome.UNDETERMINED


async def test_perform_async_zero_turns_returns_undetermined(vad_target):
    """If the session yields no Messages, executed_turns is 0 and outcome_reason explains it."""
    attack = BargeInAttack(objective_target=vad_target)
    fake = _fake_session(messages=[])

    ctx = _attack_context(audio_chunks=_aiter([b"\x00"]))

    with patch.object(RealtimeTarget, "open_streaming_session", return_value=fake):
        result = await attack._perform_async(context=ctx)

    assert result.executed_turns == 0
    assert result.outcome == AttackOutcome.UNDETERMINED
    assert result.last_response is None
    assert "No assistant turns completed" in (result.outcome_reason or "")


async def test_perform_async_propagates_session_exception(vad_target):
    """Exceptions raised inside ``session.run_async`` propagate to the caller."""
    attack = BargeInAttack(objective_target=vad_target)
    fake = _fake_session(raise_exc=RuntimeError("dispatcher blew up"))

    ctx = _attack_context(audio_chunks=_aiter([b"\x00"]))

    with patch.object(RealtimeTarget, "open_streaming_session", return_value=fake):
        with pytest.raises(RuntimeError, match="dispatcher blew up"):
            await attack._perform_async(context=ctx)


async def test_perform_async_rejects_missing_audio_chunks(vad_target):
    """``audio_chunks=None`` raises ValueError before any session is opened."""
    attack = BargeInAttack(objective_target=vad_target)
    ctx = BargeInAttackContext(params=AttackParameters(objective="obj"))
    assert ctx.audio_chunks is None

    with patch.object(RealtimeTarget, "open_streaming_session") as factory:
        with pytest.raises(ValueError, match="audio_chunks"):
            await attack._perform_async(context=ctx)

    factory.assert_not_called()
