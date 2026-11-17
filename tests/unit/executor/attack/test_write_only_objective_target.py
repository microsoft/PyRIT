# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Regression tests for attacks against write-only objective targets.

A write-only target (e.g., TextTarget) delivers the request without producing a response. Prompt sending
attacks run against it without scoring the request as response evidence, using real targets, scorers, and
memory; every other attack requires responses and rejects such a target at construction.
"""

import base64
import importlib
import inspect
import io
import json
import pkgutil
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

import pyrit.executor.attack
from pyrit.converter import Base64Converter
from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackConverterConfig,
    AttackScoringConfig,
    AttackStrategy,
    ChunkedRequestAttack,
    CrescendoAttack,
    ManyShotJailbreakAttack,
    MultiPromptSendingAttack,
    PromptSendingAttack,
    RedTeamingAttack,
    SequentialAttack,
    SkeletonKeyAttack,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.memory import SQLiteMemory
from pyrit.models import AttackOutcome, Message, MessagePiece, Score
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.prompt_target import CapabilityName, PromptTarget, TargetCapabilities, TargetConfiguration, TextTarget
from pyrit.score import MessageScorer, RegexScorer

_NO_RESPONSE_REASON = "Objective target returned no response to score"


class _EchoTarget(MockPromptTarget):
    """Replies with the request text, so a scorer matching the request also matches the response."""

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        message = normalized_conversation[-1]
        self.prompt_sent.append(message.get_value())
        return [
            MessagePiece(
                role="assistant",
                original_value=message.get_value(),
                conversation_id=message.message_pieces[0].conversation_id,
            ).to_message()
        ]


class _JsonAdversarialChat(MockPromptTarget):
    """Adversarial chat that always proposes the same next message in the adversarial_chat JSON shape."""

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        message = normalized_conversation[-1]
        self.prompt_sent.append(message.get_value())
        payload = json.dumps(
            {"next_message": "Hello from adversary", "rationale": "test", "last_response_summary": "test"}
        )
        return [
            MessagePiece(
                role="assistant",
                original_value=payload,
                conversation_id=message.message_pieces[0].conversation_id,
            ).to_message()
        ]


class _ScriptedTarget(MockPromptTarget):
    """
    Returns each scripted reply in turn; ``None`` returns no response and an exception is raised.

    By default it declares no response support, so an unanswered send is a delivery, while a send
    that does return a reply (like a storage receipt) is still processed as a response.
    """

    def __init__(self, *, replies: list[str | Exception | None], supports_responses: bool = False) -> None:
        capabilities = MockPromptTarget._DEFAULT_CONFIGURATION.capabilities
        super().__init__(
            custom_configuration=TargetConfiguration(
                capabilities=capabilities.model_copy(update={"supports_responses": supports_responses})
            )
        )
        self._replies = list(replies)

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        message = normalized_conversation[-1]
        self.prompt_sent.append(message.get_value())
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if reply is None:
            return []
        return [
            MessagePiece(
                role="assistant",
                original_value=reply,
                conversation_id=message.message_pieces[0].conversation_id,
            ).to_message()
        ]


def _matching_scorer() -> RegexScorer:
    # Matches the objective text, so scoring the sent request would report SUCCESS.
    return RegexScorer(patterns={"objective": "Hello"})


async def _all_scores_async(memory: SQLiteMemory) -> list[Score]:
    # An unfiltered query returns nothing, so filter on a time that every stored score is after.
    return list(await memory.get_scores_async(sent_after=datetime(2000, 1, 1, tzinfo=UTC), include_intermediate=True))


def _no_message_scoring() -> AbstractContextManager[AsyncMock]:
    # Every message-text scorer runs through this call, so it must never run for a delivered request.
    return patch.object(MessageScorer, "score_response_async", new_callable=AsyncMock)


async def _assert_only_requests_persisted(*, memory: SQLiteMemory, conversation_id: str, expected_count: int) -> None:
    pieces = await memory.get_message_pieces_async(conversation_id=conversation_id)
    assert [piece.role for piece in pieces] == ["user"] * expected_count
    assert await _all_scores_async(memory) == []


@pytest.mark.usefixtures("patch_central_database")
class TestPromptSendingAttackWriteOnlyTarget:
    async def test_write_only_target_is_undetermined_and_never_scored(self, sqlite_instance: SQLiteMemory) -> None:
        text_stream = io.StringIO()
        attack = PromptSendingAttack(
            objective_target=TextTarget(text_stream=text_stream),
            attack_scoring_config=AttackScoringConfig(
                objective_scorer=_matching_scorer(),
                auxiliary_scorers=[_matching_scorer()],
            ),
            max_attempts_on_failure=2,
        )

        with _no_message_scoring() as mock_score:
            result = await attack.execute_async(objective="Hello QA")

        mock_score.assert_not_awaited()
        assert result.outcome is AttackOutcome.UNDETERMINED
        assert result.outcome_reason == _NO_RESPONSE_REASON
        assert result.last_response is None
        assert result.automated_score is None
        assert result.executed_turns == 1
        # Delivered exactly once despite retries being available.
        assert text_stream.getvalue().count("Hello QA") == 1
        await _assert_only_requests_persisted(
            memory=sqlite_instance, conversation_id=result.conversation_id, expected_count=1
        )
        stored_results = await sqlite_instance.get_attack_results_async(conversation_id=result.conversation_id)
        assert [stored.outcome for stored in stored_results] == [AttackOutcome.UNDETERMINED]

    async def test_assistant_role_request_to_write_only_target_is_not_a_response(
        self, sqlite_instance: SQLiteMemory
    ) -> None:
        text_stream = io.StringIO()
        attack = PromptSendingAttack(
            objective_target=TextTarget(text_stream=text_stream),
            attack_scoring_config=AttackScoringConfig(objective_scorer=_matching_scorer()),
        )

        with _no_message_scoring() as mock_score:
            result = await attack.execute_async(
                objective="Hello QA",
                next_message=Message.from_prompt(prompt="Hello supplied request", role="assistant"),
            )

        mock_score.assert_not_awaited()
        assert result.outcome is AttackOutcome.UNDETERMINED
        assert result.outcome_reason == _NO_RESPONSE_REASON
        assert result.last_response is None
        assert result.automated_score is None
        assert text_stream.getvalue() == "assistant: Hello supplied request\n"
        assert await _all_scores_async(sqlite_instance) == []

    async def test_write_only_target_without_scorer_has_no_last_response(self, sqlite_instance: SQLiteMemory) -> None:
        attack = PromptSendingAttack(objective_target=TextTarget(text_stream=io.StringIO()))

        result = await attack.execute_async(objective="Hello QA")

        assert result.outcome is AttackOutcome.UNDETERMINED
        assert result.outcome_reason == "No objective scorer configured"
        assert result.last_response is None
        assert result.automated_score is None
        await _assert_only_requests_persisted(
            memory=sqlite_instance, conversation_id=result.conversation_id, expected_count=1
        )

    async def test_write_only_target_keeps_converted_request_export(self, sqlite_instance: SQLiteMemory) -> None:
        text_stream = io.StringIO()
        attack = PromptSendingAttack(
            objective_target=TextTarget(text_stream=text_stream),
            attack_converter_config=AttackConverterConfig(
                request_converters=ConverterConfiguration.from_converters(converters=[Base64Converter()])
            ),
            attack_scoring_config=AttackScoringConfig(objective_scorer=_matching_scorer()),
        )

        result = await attack.execute_async(objective="Hello QA")

        encoded = base64.b64encode(b"Hello QA").decode()
        assert result.outcome is AttackOutcome.UNDETERMINED
        assert text_stream.getvalue() == f"user: {encoded}\n"
        pieces = await sqlite_instance.get_message_pieces_async(conversation_id=result.conversation_id)
        assert [(piece.role, piece.original_value, piece.converted_value) for piece in pieces] == [
            ("user", "Hello QA", encoded)
        ]

    async def test_missing_reply_from_responding_target_is_an_empty_response(
        self, sqlite_instance: SQLiteMemory
    ) -> None:
        target = _ScriptedTarget(replies=[None, None, None], supports_responses=True)
        attack = PromptSendingAttack(
            objective_target=target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=_matching_scorer()),
            max_attempts_on_failure=2,
        )

        result = await attack.execute_async(objective="Hello QA")

        # The target should have replied, so the missing reply is an empty error response and is resent.
        assert len(target.prompt_sent) == 3
        assert result.outcome is AttackOutcome.UNDETERMINED
        assert result.outcome_reason is not None
        assert "Response had an error: empty" in result.outcome_reason
        assert result.last_response is not None
        assert (result.last_response.role, result.last_response.response_error) == ("assistant", "empty")

    async def test_responding_target_with_same_scorer_succeeds(self, sqlite_instance: SQLiteMemory) -> None:
        attack = PromptSendingAttack(
            objective_target=_EchoTarget(),
            attack_scoring_config=AttackScoringConfig(objective_scorer=_matching_scorer()),
        )

        result = await attack.execute_async(objective="Hello QA")

        assert result.outcome is AttackOutcome.SUCCESS
        assert result.last_response is not None
        assert result.last_response.role == "assistant"
        assert result.automated_score is not None
        assert result.automated_score.get_value() is True

    async def test_delivery_receipt_from_write_only_target_is_not_scored(self, sqlite_instance: SQLiteMemory) -> None:
        # Like AzureBlobStorageTarget, the target returns a receipt that matches the scorer but is not a reply.
        target = _ScriptedTarget(replies=["Hello receipt"])
        attack = PromptSendingAttack(
            objective_target=target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=_matching_scorer()),
            max_attempts_on_failure=2,
        )

        with _no_message_scoring() as mock_score:
            result = await attack.execute_async(objective="Hello QA")

        mock_score.assert_not_awaited()
        assert result.outcome is AttackOutcome.UNDETERMINED
        assert result.outcome_reason == _NO_RESPONSE_REASON
        assert result.last_response is None
        assert result.automated_score is None
        assert target.prompt_sent == ["Hello QA"]
        assert await _all_scores_async(sqlite_instance) == []


def _text_target(text_stream: io.StringIO, **capabilities: bool) -> TextTarget:
    # Declares the other capabilities an attack needs, so only the missing response support fails.
    return TextTarget(
        text_stream=text_stream,
        custom_configuration=TargetConfiguration(capabilities=TargetCapabilities(**capabilities)),
    )


def _adversarial_config() -> AttackAdversarialConfig:
    return AttackAdversarialConfig(target=_JsonAdversarialChat())


_MULTI_TURN = {"supports_multi_turn": True}
_CHAT_HISTORY = {"supports_multi_turn": True, "supports_editable_history": True}


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("build", "capabilities"),
    [
        pytest.param(
            lambda target: RedTeamingAttack(objective_target=target, attack_adversarial_config=_adversarial_config()),
            {},
            id="red-teaming",
        ),
        pytest.param(
            lambda target: TreeOfAttacksWithPruningAttack(
                objective_target=target, attack_adversarial_config=_adversarial_config()
            ),
            {},
            id="tree-of-attacks",
        ),
        pytest.param(
            lambda target: CrescendoAttack(objective_target=target, attack_adversarial_config=_adversarial_config()),
            _CHAT_HISTORY,
            id="crescendo",
        ),
        pytest.param(lambda target: MultiPromptSendingAttack(objective_target=target), _MULTI_TURN, id="multi-prompt"),
        pytest.param(
            lambda target: ChunkedRequestAttack(objective_target=target, chunk_size=10, total_length=30),
            _MULTI_TURN,
            id="chunked-request",
        ),
    ],
)
def test_attack_that_needs_responses_rejects_write_only_target_at_construction(
    build: Callable[[PromptTarget], AttackStrategy[Any, Any]], capabilities: dict[str, bool]
) -> None:
    text_stream = io.StringIO()

    with pytest.raises(ValueError, match="supports_responses") as exc_info:
        build(_text_target(text_stream, **capabilities))

    assert "1 required capability" in str(exc_info.value)
    assert text_stream.getvalue() == ""


# Attacks that support targets that never reply. Every other attack must require responses.
_WRITE_ONLY_CAPABLE_ATTACKS = {PromptSendingAttack, ManyShotJailbreakAttack, SkeletonKeyAttack, SequentialAttack}


def _concrete_attack_classes() -> list[type[AttackStrategy[Any, Any]]]:
    for module in pkgutil.walk_packages(pyrit.executor.attack.__path__, "pyrit.executor.attack."):
        importlib.import_module(module.name)
    pending: list[type[AttackStrategy[Any, Any]]] = [AttackStrategy]
    found: set[type[AttackStrategy[Any, Any]]] = set()
    while pending:
        for subclass in pending.pop().__subclasses__():
            pending.append(subclass)
            if subclass.__module__.startswith("pyrit.") and not inspect.isabstract(subclass):
                found.add(subclass)
    return sorted(found, key=lambda attack_class: attack_class.__qualname__)


def test_attack_discovery_finds_known_attacks() -> None:
    assert {RedTeamingAttack, TreeOfAttacksWithPruningAttack, *_WRITE_ONLY_CAPABLE_ATTACKS} <= set(
        _concrete_attack_classes()
    )


@pytest.mark.parametrize("attack_class", _concrete_attack_classes(), ids=lambda attack_class: attack_class.__name__)
def test_only_prompt_sending_attacks_accept_write_only_targets(attack_class: type[AttackStrategy[Any, Any]]) -> None:
    requires_responses = CapabilityName.RESPONSES in attack_class.TARGET_REQUIREMENTS.native_required

    assert requires_responses is (attack_class not in _WRITE_ONLY_CAPABLE_ATTACKS)
