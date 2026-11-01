# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Regression tests for attacks against write-only objective targets.

A write-only target (e.g., TextTarget) delivers the request without producing a response, and the
prompt normalizer hands the request back. These tests run real attacks, targets, scorers, and memory
to verify that the delivered request is never scored as response evidence.
"""

import base64
import io
import json
import uuid
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.converter import Base64Converter
from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackConverterConfig,
    AttackScoringConfig,
    ChunkedRequestAttack,
    MultiPromptSendingAttack,
    PromptSendingAttack,
    RedTeamingAttack,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.executor.attack.multi_turn.tree_of_attacks import TAPAttackScoringConfig
from pyrit.memory import SQLiteMemory
from pyrit.models import AttackOutcome, ComponentIdentifier, Message, MessagePiece, Score
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.prompt_target import TextTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.score import FloatScaleThresholdScorer, MessageScorer, RegexScorer

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
    """Returns each scripted reply in turn; ``None`` returns no response, like a write-only target."""

    def __init__(self, *, replies: list[str | None]) -> None:
        super().__init__()
        self._replies = list(replies)

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        message = normalized_conversation[-1]
        self.prompt_sent.append(message.get_value())
        reply = self._replies.pop(0)
        if reply is None:
            return []
        return [
            MessagePiece(
                role="assistant",
                original_value=reply,
                conversation_id=message.message_pieces[0].conversation_id,
            ).to_message()
        ]


def _multi_turn_text_target(text_stream: io.StringIO) -> TextTarget:
    # Multi-prompt and chunked attacks require native multi-turn support, which a custom
    # write-only target may declare while still returning no response.
    return TextTarget(
        text_stream=text_stream,
        custom_configuration=TargetConfiguration(capabilities=TargetCapabilities(supports_multi_turn=True)),
    )


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
        assert result.outcome_reason == _NO_RESPONSE_REASON
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


@pytest.mark.usefixtures("patch_central_database")
class TestMultiPromptSendingAttackWriteOnlyTarget:
    async def test_write_only_target_delivers_every_message_and_is_undetermined(
        self, sqlite_instance: SQLiteMemory
    ) -> None:
        text_stream = io.StringIO()
        attack = MultiPromptSendingAttack(
            objective_target=_multi_turn_text_target(text_stream),
            attack_scoring_config=AttackScoringConfig(
                objective_scorer=_matching_scorer(),
                auxiliary_scorers=[_matching_scorer()],
            ),
        )

        with _no_message_scoring() as mock_score:
            result = await attack.execute_async(
                objective="Hello QA",
                user_messages=[
                    Message.from_prompt(prompt="Hello first", role="user"),
                    Message.from_prompt(prompt="Hello second", role="user"),
                ],
            )

        mock_score.assert_not_awaited()
        assert result.outcome is AttackOutcome.UNDETERMINED
        assert result.outcome_reason == _NO_RESPONSE_REASON
        assert result.last_response is None
        assert result.automated_score is None
        assert result.executed_turns == 2
        assert text_stream.getvalue().count("Hello first") == 1
        assert text_stream.getvalue().count("Hello second") == 1
        await _assert_only_requests_persisted(
            memory=sqlite_instance, conversation_id=result.conversation_id, expected_count=2
        )

    async def test_final_response_after_earlier_delivery_is_scored(self) -> None:
        target = _ScriptedTarget(replies=[None, "Hello final"])
        attack = MultiPromptSendingAttack(
            objective_target=target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=_matching_scorer()),
        )

        result = await attack.execute_async(
            objective="Hello QA",
            user_messages=[
                Message.from_prompt(prompt="first", role="user"),
                Message.from_prompt(prompt="second", role="user"),
            ],
        )

        assert result.outcome is AttackOutcome.SUCCESS
        assert result.last_response is not None
        assert result.last_response.converted_value == "Hello final"
        assert result.automated_score is not None
        assert result.automated_score.get_value() is True
        assert result.executed_turns == 2

    async def test_final_delivery_after_earlier_response_is_undetermined(self, sqlite_instance: SQLiteMemory) -> None:
        target = _ScriptedTarget(replies=["Hello first", None])
        attack = MultiPromptSendingAttack(
            objective_target=target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=_matching_scorer()),
        )

        result = await attack.execute_async(
            objective="Hello QA",
            user_messages=[
                Message.from_prompt(prompt="first", role="user"),
                Message.from_prompt(prompt="second", role="user"),
            ],
        )

        assert result.outcome is AttackOutcome.UNDETERMINED
        assert result.outcome_reason == _NO_RESPONSE_REASON
        # The earlier response must not stand in for the unanswered final turn.
        assert result.last_response is None
        assert result.automated_score is None
        assert result.executed_turns == 2
        assert target.prompt_sent == ["first", "second"]
        assert await _all_scores_async(sqlite_instance) == []

    async def test_responding_target_with_same_scorer_succeeds(self) -> None:
        attack = MultiPromptSendingAttack(
            objective_target=_EchoTarget(),
            attack_scoring_config=AttackScoringConfig(objective_scorer=_matching_scorer()),
        )

        result = await attack.execute_async(
            objective="Hello QA",
            user_messages=[Message.from_prompt(prompt="Hello first", role="user")],
        )

        assert result.outcome is AttackOutcome.SUCCESS


@pytest.mark.usefixtures("patch_central_database")
class TestChunkedRequestAttackWriteOnlyTarget:
    async def test_write_only_target_does_not_score_requests_as_chunks(self, sqlite_instance: SQLiteMemory) -> None:
        text_stream = io.StringIO()
        attack = ChunkedRequestAttack(
            objective_target=_multi_turn_text_target(text_stream),
            chunk_size=10,
            total_length=30,
            attack_scoring_config=AttackScoringConfig(
                objective_scorer=_matching_scorer(),
                auxiliary_scorers=[_matching_scorer()],
            ),
        )

        with patch.object(attack, "_score_combined_value_async", new_callable=AsyncMock) as mock_score:
            result = await attack.execute_async(objective="Hello QA")

        mock_score.assert_not_awaited()
        assert result.outcome is AttackOutcome.UNDETERMINED
        assert result.outcome_reason == _NO_RESPONSE_REASON
        assert result.last_response is None
        assert result.automated_score is None
        assert result.executed_turns == 3
        assert result.metadata == {"combined_chunks": "\n\n", "chunk_count": 3}
        assert text_stream.getvalue().count("Hello QA") == 3
        await _assert_only_requests_persisted(
            memory=sqlite_instance, conversation_id=result.conversation_id, expected_count=3
        )

    async def test_received_chunks_are_scored_when_other_chunks_were_only_delivered(self) -> None:
        attack = ChunkedRequestAttack(
            objective_target=_ScriptedTarget(replies=[None, "Hello", None]),
            chunk_size=10,
            total_length=30,
            attack_scoring_config=AttackScoringConfig(objective_scorer=_matching_scorer()),
        )

        result = await attack.execute_async(objective="Hello QA")

        assert result.outcome is AttackOutcome.SUCCESS
        assert result.automated_score is not None
        assert result.automated_score.get_value() is True
        assert result.metadata == {"combined_chunks": "\nHello\n", "chunk_count": 3}
        assert result.executed_turns == 3

    async def test_responding_target_with_same_scorer_succeeds(self) -> None:
        attack = ChunkedRequestAttack(
            objective_target=_EchoTarget(),
            chunk_size=10,
            total_length=30,
            attack_scoring_config=AttackScoringConfig(objective_scorer=_matching_scorer()),
        )

        result = await attack.execute_async(objective="Hello QA")

        assert result.outcome is AttackOutcome.SUCCESS


@pytest.mark.usefixtures("patch_central_database")
class TestRedTeamingAttackWriteOnlyTarget:
    async def test_write_only_target_raises_instead_of_scoring_the_request(self, sqlite_instance: SQLiteMemory) -> None:
        text_stream = io.StringIO()
        adversarial_chat = _JsonAdversarialChat()
        attack = RedTeamingAttack(
            objective_target=TextTarget(text_stream=text_stream),
            attack_adversarial_config=AttackAdversarialConfig(target=adversarial_chat),
            attack_scoring_config=AttackScoringConfig(objective_scorer=_matching_scorer()),
            max_turns=2,
        )

        with pytest.raises(RuntimeError, match="cannot run against a write-only target") as exc_info:
            await attack.execute_async(objective="Hello QA")

        assert isinstance(exc_info.value.__cause__, ValueError)
        assert len(adversarial_chat.prompt_sent) == 1
        assert text_stream.getvalue().count("Hello from adversary") == 1
        assert await _all_scores_async(sqlite_instance) == []


def _always_true_threshold_scorer() -> MagicMock:
    # Reports success for anything it is given, so scoring a delivered request would be a false SUCCESS.
    objective_scorer = MagicMock(spec=FloatScaleThresholdScorer)
    objective_scorer.threshold = 0.8
    objective_scorer.get_identifier.return_value = ComponentIdentifier(
        class_name="FloatScaleThresholdScorer", class_module="test_module"
    )
    objective_scorer.score_async = AsyncMock(
        return_value=[
            Score(
                score_type="true_false",
                score_value="true",
                score_category=["test"],
                score_value_description="Objective achieved",
                score_rationale="Matched",
                score_metadata={},
                message_piece_id=str(uuid.uuid4()),
                scorer_class_identifier=objective_scorer.get_identifier.return_value,
            )
        ]
    )
    return objective_scorer


@pytest.mark.usefixtures("patch_central_database")
class TestTreeOfAttacksWriteOnlyTarget:
    async def test_write_only_target_is_undetermined_and_never_scored(self, sqlite_instance: SQLiteMemory) -> None:
        objective_scorer = _always_true_threshold_scorer()
        text_stream = io.StringIO()
        attack = TreeOfAttacksWithPruningAttack(
            objective_target=TextTarget(text_stream=text_stream),
            attack_adversarial_config=AttackAdversarialConfig(target=_JsonAdversarialChat()),
            attack_scoring_config=TAPAttackScoringConfig(objective_scorer=objective_scorer),
            tree_width=1,
            tree_depth=2,
            branching_factor=1,
            on_topic_checking_enabled=False,
        )

        result = await attack.execute_async(objective="Hello QA")

        objective_scorer.score_async.assert_not_awaited()
        assert result.outcome is AttackOutcome.UNDETERMINED
        assert result.outcome_reason == _NO_RESPONSE_REASON
        assert result.last_response is None
        assert result.automated_score is None
        assert text_stream.getvalue().count("Hello from adversary") == 1
        # The objective conversation holds only the delivered request, with no fabricated response.
        delivered = [
            piece
            for piece in await sqlite_instance.get_message_pieces_async()
            if piece.converted_value == "Hello from adversary"
        ]
        assert [piece.role for piece in delivered] == ["user"]
        objective_pieces = await sqlite_instance.get_message_pieces_async(conversation_id=delivered[0].conversation_id)
        assert [piece.role for piece in objective_pieces] == ["user"]
        assert await _all_scores_async(sqlite_instance) == []

    async def test_unanswered_branch_does_not_discard_scored_sibling(self, sqlite_instance: SQLiteMemory) -> None:
        objective_scorer = _always_true_threshold_scorer()
        target = _ScriptedTarget(replies=["Hello actual response", None])
        attack = TreeOfAttacksWithPruningAttack(
            objective_target=target,
            attack_adversarial_config=AttackAdversarialConfig(target=_JsonAdversarialChat()),
            attack_scoring_config=TAPAttackScoringConfig(objective_scorer=objective_scorer),
            tree_width=2,
            tree_depth=1,
            branching_factor=1,
            batch_size=1,
            on_topic_checking_enabled=False,
        )

        result = await attack.execute_async(objective="Hello QA")

        assert result.outcome is AttackOutcome.SUCCESS
        assert result.automated_score is not None
        # The selected conversation is the answered branch, not the unanswered one.
        pieces = await sqlite_instance.get_message_pieces_async(conversation_id=result.conversation_id)
        assert [(piece.role, piece.converted_value) for piece in pieces][-1] == ("assistant", "Hello actual response")
        # Only the answered branch was scored.
        assert objective_scorer.score_async.await_count == 1
        assert len(target.prompt_sent) == 2
