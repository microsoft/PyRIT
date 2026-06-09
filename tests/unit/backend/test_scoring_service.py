# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the scoring service.

Mocks ``ScorerRegistry``, ``CentralMemory``, and the per-scorer ``score_async`` to
exercise the orchestration logic in isolation.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.models.scoring import (
    ScoreConversationRequest,
    ScoreMessageRequest,
)
from pyrit.backend.services.scoring_service import (
    ScoringService,
    get_scoring_service,
)
from pyrit.models import AttackOutcome, AttackResult, ComponentIdentifier, build_atomic_attack_identifier
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


@pytest.fixture
def mock_memory():
    memory = MagicMock()
    memory.get_attack_results.return_value = []
    memory.get_conversation.return_value = []
    return memory


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    registry.get.return_value = None
    registry.get_all_instances.return_value = []
    return registry


@pytest.fixture
def scoring_service(mock_memory, mock_registry):
    with patch("pyrit.backend.services.scoring_service.CentralMemory") as mock_central, patch(
        "pyrit.backend.services.scoring_service.ScorerRegistry"
    ) as mock_registry_cls:
        mock_central.get_memory_instance.return_value = mock_memory
        mock_registry_cls.get_registry_singleton.return_value = mock_registry
        # Bypass lru_cache so each test gets a fresh service instance bound to the mocks above.
        get_scoring_service.cache_clear()
        service = ScoringService()
        yield service
        get_scoring_service.cache_clear()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_attack_result(*, conversation_id: str = "conv-1", attack_result_id: str = "ar-1") -> AttackResult:
    target_identifier = ComponentIdentifier(
        class_name="TextTarget",
        class_module="pyrit.prompt_target",
    )
    now = datetime.now(timezone.utc)
    return AttackResult(
        conversation_id=conversation_id,
        objective="Test",
        atomic_attack_identifier=build_atomic_attack_identifier(
            attack_identifier=ComponentIdentifier(
                class_name="ManualAttack",
                class_module="pyrit.backend",
                children={"objective_target": target_identifier},
            ),
        ),
        outcome=AttackOutcome.UNDETERMINED,
        attack_result_id=attack_result_id,
        metadata={"created_at": now.isoformat(), "updated_at": now.isoformat()},
        labels={},
    )


def _make_piece(*, role: str = "assistant", piece_id: str | None = None) -> MagicMock:
    piece = MagicMock()
    piece.id = piece_id or uuid.uuid4()
    piece.role = role
    piece.api_role = "assistant" if role in ("assistant", "simulated_assistant") else role
    piece.scores = []
    return piece


def _make_message(pieces: list[MagicMock]) -> MagicMock:
    msg = MagicMock()
    msg.message_pieces = pieces
    return msg


def _make_pyrit_score(*, value: str = "true", category: str = "harm") -> MagicMock:
    score = MagicMock()
    score.id = uuid.uuid4()
    score.scorer_class_identifier = ComponentIdentifier(
        class_name="FakeScorer",
        class_module="tests",
    )
    score.score_type = "true_false"
    score.score_value = value
    score.score_category = [category]
    score.score_rationale = "because"
    score.timestamp = datetime.now(timezone.utc)
    return score


# --------------------------------------------------------------------------- #
# list_scorers_async
# --------------------------------------------------------------------------- #


class TestListScorers:
    async def test_returns_empty_when_no_scorers(self, scoring_service, mock_registry) -> None:
        mock_registry.get_all_instances.return_value = []

        result = await scoring_service.list_scorers_async()

        assert result.items == []

    async def test_returns_registered_scorers(self, scoring_service, mock_registry) -> None:
        scorer = MagicMock(spec=TrueFalseScorer)
        scorer.scorer_type = "true_false"
        entry = MagicMock()
        entry.name = "my-scorer"
        entry.instance = scorer
        entry.tags = {"refusal": "", "best_refusal": ""}
        mock_registry.get_all_instances.return_value = [entry]

        result = await scoring_service.list_scorers_async()

        assert len(result.items) == 1
        item = result.items[0]
        assert item.scorer_registry_name == "my-scorer"
        assert item.score_type == "true_false"
        assert sorted(item.tags) == ["best_refusal", "refusal"]
        # MagicMock(spec=TrueFalseScorer) inherits TrueFalseScorer.__doc__,
        # so description should come from the real class docstring (first paragraph).
        assert item.description and len(item.description) > 0

    async def test_description_falls_back_to_none_when_class_has_no_docstring(
        self, scoring_service, mock_registry
    ) -> None:
        class _Undocumented:
            pass

        scorer = MagicMock()
        scorer.scorer_type = "true_false"
        scorer.__class__ = _Undocumented
        entry = MagicMock()
        entry.name = "undoc"
        entry.instance = scorer
        entry.tags = {}
        mock_registry.get_all_instances.return_value = [entry]

        result = await scoring_service.list_scorers_async()
        assert result.items[0].description is None
        assert result.items[0].tags == []


# --------------------------------------------------------------------------- #
# score_conversation_async
# --------------------------------------------------------------------------- #


class TestScoreConversation:
    async def test_raises_when_attack_missing(self, scoring_service, mock_memory) -> None:
        mock_memory.get_attack_results.return_value = []

        with pytest.raises(LookupError, match="not found"):
            await scoring_service.score_conversation_async(
                attack_result_id="missing",
                conversation_id="conv-1",
                request=ScoreConversationRequest(scorer_registry_name="x"),
            )

    async def test_raises_when_conversation_not_in_attack(self, scoring_service, mock_memory) -> None:
        mock_memory.get_attack_results.return_value = [_make_attack_result(conversation_id="conv-1")]

        with pytest.raises(ValueError, match="not part of attack"):
            await scoring_service.score_conversation_async(
                attack_result_id="ar-1",
                conversation_id="other-conv",
                request=ScoreConversationRequest(scorer_registry_name="x"),
            )

    async def test_raises_when_scorer_missing(
        self, scoring_service, mock_memory, mock_registry
    ) -> None:
        mock_memory.get_attack_results.return_value = [_make_attack_result()]
        mock_registry.get.return_value = None

        with pytest.raises(ValueError, match="not registered"):
            await scoring_service.score_conversation_async(
                attack_result_id="ar-1",
                conversation_id="conv-1",
                request=ScoreConversationRequest(scorer_registry_name="missing-scorer"),
            )

    async def test_raises_when_conversation_empty(
        self, scoring_service, mock_memory, mock_registry
    ) -> None:
        mock_memory.get_attack_results.return_value = [_make_attack_result()]
        mock_memory.get_conversation.return_value = []
        mock_registry.get.return_value = MagicMock(spec=TrueFalseScorer)

        with pytest.raises(ValueError, match="no messages to score"):
            await scoring_service.score_conversation_async(
                attack_result_id="ar-1",
                conversation_id="conv-1",
                request=ScoreConversationRequest(scorer_registry_name="x"),
            )

    async def test_raises_when_last_message_has_no_assistant_turn(
        self, scoring_service, mock_memory, mock_registry
    ) -> None:
        mock_memory.get_attack_results.return_value = [_make_attack_result()]
        mock_memory.get_conversation.return_value = [_make_message([_make_piece(role="user")])]
        mock_registry.get.return_value = MagicMock(spec=TrueFalseScorer)

        with pytest.raises(ValueError, match="no assistant message"):
            await scoring_service.score_conversation_async(
                attack_result_id="ar-1",
                conversation_id="conv-1",
                request=ScoreConversationRequest(scorer_registry_name="x"),
            )

    async def test_last_message_scores_most_recent_assistant_turn(
        self, scoring_service, mock_memory, mock_registry
    ) -> None:
        user_msg = _make_message([_make_piece(role="user")])
        first_assistant = _make_message([_make_piece(role="assistant")])
        last_assistant = _make_message([_make_piece(role="assistant")])
        trailing_user = _make_message([_make_piece(role="user")])
        mock_memory.get_attack_results.return_value = [_make_attack_result()]
        mock_memory.get_conversation.return_value = [user_msg, first_assistant, user_msg, last_assistant, trailing_user]

        scorer = MagicMock(spec=TrueFalseScorer)
        scorer.score_async = AsyncMock(return_value=[_make_pyrit_score()])
        mock_registry.get.return_value = scorer

        result = await scoring_service.score_conversation_async(
            attack_result_id="ar-1",
            conversation_id="conv-1",
            request=ScoreConversationRequest(scorer_registry_name="my-scorer", objective="be helpful"),
        )

        scorer.score_async.assert_awaited_once()
        kwargs = scorer.score_async.await_args.kwargs
        assert kwargs["message"] is last_assistant
        assert kwargs["objective"] == "be helpful"
        assert len(result.scores) == 1
        assert result.scores[0].score_value == "true"

    async def test_whole_conversation_wraps_scorer(
        self, scoring_service, mock_memory, mock_registry
    ) -> None:
        mock_memory.get_attack_results.return_value = [_make_attack_result()]
        # Whole-conv mode just hands the last message to the wrapped scorer; content doesn't matter.
        last = _make_message([_make_piece(role="assistant")])
        mock_memory.get_conversation.return_value = [last]

        scorer = MagicMock(spec=FloatScaleScorer)
        mock_registry.get.return_value = scorer

        with patch(
            "pyrit.score.conversation_scorer.create_conversation_scorer"
        ) as mock_create:
            wrapped = MagicMock()
            wrapped.score_async = AsyncMock(return_value=[_make_pyrit_score()])
            mock_create.return_value = wrapped

            await scoring_service.score_conversation_async(
                attack_result_id="ar-1",
                conversation_id="conv-1",
                request=ScoreConversationRequest(
                    scorer_registry_name="my-scorer", mode="whole_conversation"
                ),
            )

            mock_create.assert_called_once_with(scorer=scorer)
            wrapped.score_async.assert_awaited_once()

    async def test_whole_conversation_rejects_unsupported_scorer(
        self, scoring_service, mock_memory, mock_registry
    ) -> None:
        mock_memory.get_attack_results.return_value = [_make_attack_result()]
        mock_memory.get_conversation.return_value = [_make_message([_make_piece(role="assistant")])]
        mock_registry.get.return_value = MagicMock()  # Not a FloatScale/TrueFalse scorer.

        with pytest.raises(ValueError, match="FloatScaleScorer or TrueFalseScorer"):
            await scoring_service.score_conversation_async(
                attack_result_id="ar-1",
                conversation_id="conv-1",
                request=ScoreConversationRequest(
                    scorer_registry_name="my-scorer", mode="whole_conversation"
                ),
            )


# --------------------------------------------------------------------------- #
# score_message_async
# --------------------------------------------------------------------------- #


class TestScoreMessage:
    async def test_scores_specific_piece(self, scoring_service, mock_memory, mock_registry) -> None:
        target_piece = _make_piece(role="assistant", piece_id="piece-target")
        other_piece = _make_piece(role="assistant", piece_id="piece-other")
        target_msg = _make_message([target_piece])
        other_msg = _make_message([other_piece])

        mock_memory.get_attack_results.return_value = [_make_attack_result()]
        mock_memory.get_conversation.return_value = [other_msg, target_msg]

        scorer = MagicMock(spec=TrueFalseScorer)
        scorer.score_async = AsyncMock(return_value=[_make_pyrit_score()])
        mock_registry.get.return_value = scorer

        result = await scoring_service.score_message_async(
            attack_result_id="ar-1",
            conversation_id="conv-1",
            piece_id="piece-target",
            request=ScoreMessageRequest(scorer_registry_name="my-scorer"),
        )

        scorer.score_async.assert_awaited_once()
        assert scorer.score_async.await_args.kwargs["message"] is target_msg
        assert len(result.scores) == 1

    async def test_raises_when_piece_not_in_conversation(
        self, scoring_service, mock_memory, mock_registry
    ) -> None:
        mock_memory.get_attack_results.return_value = [_make_attack_result()]
        mock_memory.get_conversation.return_value = [
            _make_message([_make_piece(role="assistant", piece_id="other")])
        ]
        mock_registry.get.return_value = MagicMock(spec=TrueFalseScorer)

        with pytest.raises(LookupError, match="not part of conversation"):
            await scoring_service.score_message_async(
                attack_result_id="ar-1",
                conversation_id="conv-1",
                piece_id="missing-piece",
                request=ScoreMessageRequest(scorer_registry_name="x"),
            )
