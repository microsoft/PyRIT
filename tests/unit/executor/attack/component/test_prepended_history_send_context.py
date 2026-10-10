# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid

import pytest

from pyrit.executor.attack.component.prepended_history_send_context import (
    PrependedHistorySendContext,
)
from pyrit.models import ChatMessageRole, Message


def _message(
    *,
    role: ChatMessageRole,
    value: str,
    sequence: int,
    conversation_id: str = "conversation",
) -> Message:
    message = Message.from_prompt(prompt=value, role=role)
    piece = message.get_piece()
    piece.sequence = sequence
    piece.conversation_id = conversation_id
    return message


def test_context_requires_unique_persisted_seed_ids() -> None:
    message_id = uuid.uuid4()

    with pytest.raises(ValueError, match="conversation_id"):
        PrependedHistorySendContext(
            conversation_id="",
            seed_message_ids=(message_id,),
            replay_seed_each_send=False,
        )
    with pytest.raises(ValueError, match="seed_message_ids"):
        PrependedHistorySendContext(
            conversation_id="conversation",
            seed_message_ids=(),
            replay_seed_each_send=False,
        )
    with pytest.raises(ValueError, match="unique"):
        PrependedHistorySendContext(
            conversation_id="conversation",
            seed_message_ids=(message_id, message_id),
            replay_seed_each_send=False,
        )


def test_stateful_context_consumes_explicit_boundary_at_target_invocation() -> None:
    first = _message(role="system", value="system", sequence=0)
    second = _message(role="user", value="seed", sequence=1)
    unrelated = _message(role="assistant", value="later response", sequence=2)
    context = PrependedHistorySendContext(
        conversation_id="conversation",
        seed_message_ids=(first.get_piece().id, second.get_piece().id),
        replay_seed_each_send=False,
    )

    context.begin_send()
    selected = context.select_history(messages=[unrelated, second, first])
    context.mark_target_invoked()
    assert not context.is_seed_consumed
    context.finish_send(succeeded=True)

    assert selected == [first, second]
    assert context.is_seed_consumed
    assert context.target_invocation_count == 1

    context.begin_send()
    assert context.select_history(messages=[first, second, unrelated]) == [first, second, unrelated]
    context.finish_send(succeeded=True)


def test_stateless_context_replays_explicit_boundary_after_target_invocation() -> None:
    seed = _message(role="user", value="seed", sequence=0)
    context = PrependedHistorySendContext(
        conversation_id="conversation",
        seed_message_ids=(seed.get_piece().id,),
        replay_seed_each_send=True,
    )

    for _ in range(2):
        context.begin_send()
        assert context.select_history(messages=[seed]) == [seed]
        context.mark_target_invoked()
        context.finish_send(succeeded=True)

    assert not context.is_seed_consumed
    assert context.target_invocation_count == 2


def test_context_rejects_concurrent_send_until_active_send_finishes() -> None:
    context = PrependedHistorySendContext(
        conversation_id="conversation",
        seed_message_ids=(uuid.uuid4(),),
        replay_seed_each_send=False,
    )

    context.begin_send()
    with pytest.raises(RuntimeError, match="Concurrent sends"):
        context.begin_send()

    context.finish_send(succeeded=False)
    context.begin_send()
    context.finish_send(succeeded=False)


def test_context_rejects_target_invocation_without_active_send() -> None:
    context = PrependedHistorySendContext(
        conversation_id="conversation",
        seed_message_ids=(uuid.uuid4(),),
        replay_seed_each_send=False,
    )

    with pytest.raises(RuntimeError, match="without an active send"):
        context.mark_target_invoked()


def test_context_counts_one_target_invocation_per_send() -> None:
    context = PrependedHistorySendContext(
        conversation_id="conversation",
        seed_message_ids=(uuid.uuid4(),),
        replay_seed_each_send=False,
    )

    context.begin_send()
    context.mark_target_invoked()
    context.mark_target_invoked()
    context.finish_send(succeeded=True)

    assert context.target_invocation_count == 1


def test_context_rejects_missing_persisted_boundary_message() -> None:
    context = PrependedHistorySendContext(
        conversation_id="conversation",
        seed_message_ids=(uuid.uuid4(),),
        replay_seed_each_send=False,
    )

    with pytest.raises(ValueError, match="Missing 1 message"):
        context.select_history(messages=[])


def test_context_remaps_only_explicit_seed_for_duplicate_conversation() -> None:
    seed = _message(role="system", value="seed", sequence=0)
    live_request = _message(role="user", value="live", sequence=1)
    source_messages = [seed, live_request]
    duplicated_messages = [message.duplicate() for message in source_messages]
    for message in duplicated_messages:
        for piece in message.message_pieces:
            piece.conversation_id = "duplicate"

    context = PrependedHistorySendContext(
        conversation_id="conversation",
        seed_message_ids=(seed.get_piece().id,),
        replay_seed_each_send=True,
    )

    duplicate = context.remap_for_duplicate_conversation(
        conversation_id="duplicate",
        source_messages=source_messages,
        duplicated_messages=duplicated_messages,
    )

    assert duplicate.seed_message_ids == (duplicated_messages[0].get_piece().id,)
    assert duplicate.bootstrap_message_ids == (duplicated_messages[0].get_piece().id,)
    assert duplicated_messages[1].get_piece().id not in duplicate.seed_message_ids
    assert duplicate.select_history(messages=duplicated_messages) == [duplicated_messages[0]]


def test_stateful_context_bootstraps_full_duplicated_branch() -> None:
    seed = _message(role="system", value="seed", sequence=0)
    live_request = _message(role="user", value="live", sequence=1)
    response = _message(role="assistant", value="response", sequence=2)
    source_messages = [seed, live_request, response]
    duplicated_messages = [
        _message(role=message.api_role, value=message.get_value(), sequence=message.sequence)
        for message in source_messages
    ]
    for message in duplicated_messages:
        message.get_piece().conversation_id = "duplicate"

    context = PrependedHistorySendContext(
        conversation_id="source",
        seed_message_ids=(seed.get_piece().id,),
        replay_seed_each_send=False,
    )

    duplicate = context.remap_for_duplicate_conversation(
        conversation_id="duplicate",
        source_messages=source_messages,
        duplicated_messages=duplicated_messages,
    )

    assert duplicate.seed_message_ids == (duplicated_messages[0].get_piece().id,)
    assert duplicate.bootstrap_message_ids == tuple(message.get_piece().id for message in duplicated_messages)
    assert duplicate.select_history(messages=duplicated_messages) == duplicated_messages


def test_context_remap_resets_consumed_state_for_new_conversation() -> None:
    seed = _message(role="user", value="seed", sequence=0)
    duplicated_seed = seed.duplicate()
    duplicated_seed.get_piece().conversation_id = "duplicate"
    context = PrependedHistorySendContext(
        conversation_id="conversation",
        seed_message_ids=(seed.get_piece().id,),
        replay_seed_each_send=False,
    )
    context.begin_send()
    context.mark_target_invoked()
    context.finish_send(succeeded=True)

    duplicate = context.remap_for_duplicate_conversation(
        conversation_id="duplicate",
        source_messages=[seed],
        duplicated_messages=[duplicated_seed],
    )

    assert not duplicate.is_seed_consumed
    assert duplicate.select_history(messages=[duplicated_seed]) == [duplicated_seed]
