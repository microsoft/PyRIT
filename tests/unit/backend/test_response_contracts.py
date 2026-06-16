# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
JSON/schema contract tests for the backend response views.

These guard the serialized wire shape of the canonical-model-backed response
DTOs (``ScoreView``/``MessagePieceView``/``MessageView``/``AttackSummary``):
canonical fields plus presentation computed fields must appear in
``model_dump(mode="json")``, ``related_conversations`` must serialize in a
stable (sorted) order, and the deprecated wire aliases (``score_id``,
``scored_at``, ``piece_id``, ``pieces``) must stay populated for back-compat.
"""

import uuid
from datetime import datetime, timezone

from pyrit.backend.models.attacks import (
    AttackSummary,
    MessagePieceView,
    MessageView,
    ScoreView,
)
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackResult,
    ComponentIdentifier,
    MessagePiece,
    RetryEvent,
    Score,
)
from pyrit.models.conversation_reference import ConversationReference, ConversationType


def _make_score() -> Score:
    return Score(
        score_value="0.5",
        score_type="float_scale",
        score_rationale="because",
        message_piece_id=str(uuid.uuid4()),
        scorer_class_identifier=ComponentIdentifier(class_name="FloatScaleScorer", class_module="pyrit.score"),
    )


def _make_piece(*, sequence: int = 0, role: str = "user") -> MessagePiece:
    return MessagePiece(
        role=role,
        original_value="hello",
        converted_value="hello",
        original_value_data_type="text",
        converted_value_data_type="text",
        conversation_id="conv-1",
        sequence=sequence,
    )


def _make_attack_result(*, name: str = "CrescendoAttack") -> AttackResult:
    target = ComponentIdentifier(class_name="OpenAIChatTarget", class_module="pyrit.prompt_target")
    return AttackResult(
        conversation_id="attack-1",
        objective="test objective",
        attack_result_id="ar-attack-1",
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=ComponentIdentifier(
                class_name=name,
                class_module="pyrit.attacks",
                children={"objective_target": target},
            ),
        ),
    )


class TestScoreViewContract:
    """JSON contract for ScoreView."""

    def test_dump_has_canonical_and_computed_fields(self) -> None:
        """Test that the serialized score exposes canonical fields plus scorer_type."""
        view = ScoreView.from_domain(_make_score())
        dumped = view.model_dump(mode="json")

        assert dumped["score_value"] == "0.5"
        assert dumped["score_type"] == "float_scale"
        assert dumped["scorer_type"] == "FloatScaleScorer"
        assert "scorer_class_identifier" in dumped

    def test_schema_builds(self) -> None:
        """Test that ScoreView's serialization schema includes the computed field."""
        assert "scorer_type" in ScoreView.model_json_schema(mode="serialization")["properties"]


class TestMessagePieceViewContract:
    """JSON contract for MessagePieceView."""

    def test_dump_has_canonical_and_presentation_fields(self) -> None:
        """Test that the serialized piece exposes canonical and derived presentation fields."""
        piece = _make_piece()
        view = MessagePieceView.from_domain(piece)
        dumped = view.model_dump(mode="json")

        assert dumped["role"] == "user"
        assert dumped["original_value"] == "hello"
        assert "original_value_url" in dumped
        assert "converted_value_url" in dumped
        assert "original_value_mime_type" in dumped
        assert "converted_value_mime_type" in dumped
        assert "original_filename" in dumped
        assert "converted_filename" in dumped
        assert "response_error_description" in dumped
        assert dumped["scores"] == []

    def test_scores_are_score_views(self) -> None:
        """Test that nested scores serialize with the ScoreView computed field."""
        piece = _make_piece()
        view = MessagePieceView.from_domain(piece, scores=[_make_score()])
        dumped = view.model_dump(mode="json")

        assert dumped["scores"][0]["scorer_type"] == "FloatScaleScorer"


class TestMessageViewContract:
    """JSON contract for MessageView."""

    def test_dump_has_turn_metadata_and_pieces(self) -> None:
        """Test that the serialized message exposes turn metadata and piece views."""
        piece = MessagePieceView.from_domain(_make_piece(sequence=3, role="assistant"))
        view = MessageView.model_construct(message_pieces=[piece])
        dumped = view.model_dump(mode="json")

        assert dumped["turn_number"] == 3
        assert dumped["role"] == "assistant"
        assert "created_at" in dumped
        assert len(dumped["message_pieces"]) == 1
        assert dumped["message_pieces"][0]["role"] == "assistant"


class TestAttackSummaryContract:
    """JSON contract for AttackSummary, including set-ordering (R1)."""

    def _summary(self, ar: AttackResult) -> AttackSummary:
        now = datetime.now(timezone.utc)
        data = {name: getattr(ar, name) for name in AttackResult.model_fields}
        data.update(
            last_response=None,
            last_score=None,
            labels={"env": "prod"},
            message_count=2,
            last_message_preview="hi",
            created_at=now,
            updated_at=now,
        )
        return AttackSummary.model_construct(**data)

    def test_dump_has_canonical_computed_and_stats_fields(self) -> None:
        """Test that the serialized summary exposes canonical, computed, and stats fields."""
        dumped = self._summary(_make_attack_result()).model_dump(mode="json")

        assert dumped["conversation_id"] == "attack-1"
        assert dumped["objective"] == "test objective"
        assert dumped["attack_type"] == "CrescendoAttack"
        assert dumped["target"]["target_type"] == "OpenAIChatTarget"
        assert dumped["converters"] == []
        assert dumped["message_count"] == 2
        assert dumped["last_message_preview"] == "hi"
        assert dumped["labels"] == {"env": "prod"}
        assert "retry_events" in dumped

    def test_related_conversations_serialize_sorted(self) -> None:
        """Test that related_conversations serialize in a stable, sorted order (R1)."""
        ar = _make_attack_result()
        ar.related_conversations = {
            ConversationReference(conversation_id="zeta", conversation_type=ConversationType.PRUNED),
            ConversationReference(conversation_id="alpha", conversation_type=ConversationType.ADVERSARIAL),
            ConversationReference(conversation_id="mid", conversation_type=ConversationType.PRUNED),
        }

        dumped = self._summary(ar).model_dump(mode="json")

        ordered_ids = [ref["conversation_id"] for ref in dumped["related_conversations"]]
        assert ordered_ids == ["alpha", "mid", "zeta"]
        assert dumped["related_conversation_ids"] == ["alpha", "mid", "zeta"]

    def test_retry_events_round_trip(self) -> None:
        """Test that inherited retry_events serialize with their canonical payload."""
        ar = _make_attack_result()
        ar.retry_events = [RetryEvent(attempt_number=1, exception_type="RateLimitError")]

        dumped = self._summary(ar).model_dump(mode="json")

        assert dumped["retry_events"][0]["attempt_number"] == 1
        assert dumped["retry_events"][0]["exception_type"] == "RateLimitError"


class TestDeprecatedWireAliases:
    """Old wire field names stay populated (as deprecated aliases) for backward compat."""

    def test_score_view_emits_deprecated_aliases(self) -> None:
        """Test that ScoreView still emits score_id/scored_at mirroring id/timestamp."""
        view = ScoreView.from_domain(_make_score())
        dumped = view.model_dump(mode="json")

        assert dumped["score_id"] == str(view.id)
        assert dumped["scored_at"] == dumped["timestamp"]

    def test_message_piece_view_emits_deprecated_alias(self) -> None:
        """Test that MessagePieceView still emits piece_id mirroring id."""
        view = MessagePieceView.from_domain(_make_piece())
        dumped = view.model_dump(mode="json")

        assert dumped["piece_id"] == str(view.id)

    def test_message_view_does_not_emit_pieces_alias(self) -> None:
        """The deprecated ``pieces`` alias was dropped; only ``message_pieces`` is emitted."""
        piece = MessagePieceView.from_domain(_make_piece())
        dumped = MessageView.model_construct(message_pieces=[piece]).model_dump(mode="json")

        assert "pieces" not in dumped
        assert "message_pieces" in dumped

    def test_aliases_marked_deprecated_in_schema(self) -> None:
        """Test that the deprecated aliases are flagged deprecated in the OpenAPI schema."""
        score_props = ScoreView.model_json_schema(mode="serialization")["properties"]
        piece_props = MessagePieceView.model_json_schema(mode="serialization")["properties"]
        message_props = MessageView.model_json_schema(mode="serialization")["properties"]

        assert score_props["score_id"]["deprecated"] is True
        assert score_props["scored_at"]["deprecated"] is True
        assert piece_props["piece_id"]["deprecated"] is True
        assert "pieces" not in message_props
