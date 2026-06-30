# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend mapper functions.

These tests verify the domain ↔ DTO translation layer in isolation,
without any database or service dependencies.
"""

import os
import tempfile
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.mappers.attack_mappers import (
    _is_azure_blob_url,
    _resolve_media_url,
    _sign_blob_url_async,
    attack_result_to_summary_async,
    pyrit_messages_to_dto_async,
    request_piece_to_pyrit_message_piece,
    request_to_pyrit_message,
)
from pyrit.backend.mappers.converter_mappers import converter_object_to_instance
from pyrit.backend.mappers.target_mappers import target_object_to_instance
from pyrit.backend.models._media import build_filename, infer_mime_type
from pyrit.backend.models.attacks import ScoreView
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    Message,
    MessagePiece,
    Score,
)
from pyrit.models.conversation_stats import ConversationStats
from pyrit.prompt_target import PromptTarget, TargetCapabilities

# ============================================================================
# Helpers
# ============================================================================


def _make_attack_result(
    *,
    conversation_id: str = "attack-1",
    has_target: bool = True,
    name: str = "Test Attack",
    outcome: AttackOutcome = AttackOutcome.UNDETERMINED,
) -> AttackResult:
    """Create an AttackResult for mapper tests."""
    now = datetime.now(timezone.utc)

    target_identifier = (
        ComponentIdentifier(
            class_name="TextTarget",
            class_module="pyrit.prompt_target",
        )
        if has_target
        else None
    )

    children = {}
    if target_identifier:
        children["objective_target"] = target_identifier

    return AttackResult(
        conversation_id=conversation_id,
        objective="test",
        attack_result_id=str(uuid.uuid4()),
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=ComponentIdentifier(
                class_name=name,
                class_module="pyrit.backend",
                params={
                    "source": "gui",
                },
                children=children,
            )
        ),
        outcome=outcome,
        metadata={
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        },
        labels={"test_ar_label": "test_ar_value"},
    )


def _make_piece(
    *,
    sequence: int = 0,
    converted_value: str = "hello",
    original_value: str = "hello",
    original_value_data_type: str = "text",
    converted_value_data_type: str = "text",
    role: str = "user",
) -> MessagePiece:
    """Create a real domain message piece for mapper tests."""
    return MessagePiece(
        role=role,
        original_value=original_value,
        converted_value=converted_value,
        original_value_data_type=original_value_data_type,
        converted_value_data_type=converted_value_data_type,
        conversation_id="conv-1",
        sequence=sequence,
    )


def _make_score(
    *,
    score_value: str = "1.0",
    score_type: str = "float_scale",
    score_category: list[str] | None = None,
    scorer_name: str = "TrueFalseScorer",
) -> Score:
    """Create a real domain score for mapper tests."""
    return Score(
        score_value=score_value,
        score_type=score_type,
        score_category=score_category,
        score_rationale="Looks correct",
        message_piece_id=str(uuid.uuid4()),
        scorer_class_identifier=ComponentIdentifier(
            class_name=scorer_name,
            class_module="pyrit.score",
        ),
    )


# ============================================================================
# Attack Mapper Tests
# ============================================================================


class TestAttackResultToSummary:
    """Tests for attack_result_to_summary_async function."""

    async def test_basic_mapping(self) -> None:
        """Test that all fields are mapped correctly."""
        ar = _make_attack_result(name="My Attack")
        stats = ConversationStats(message_count=2)

        summary = await attack_result_to_summary_async(ar, stats=stats)

        assert summary.conversation_id == ar.conversation_id
        assert summary.outcome == "undetermined"
        assert summary.message_count == 2
        # Attack metadata should be extracted into explicit fields
        assert summary.attack_type == "My Attack"
        assert summary.target is not None
        assert summary.target.target_type == "TextTarget"

    async def test_empty_pieces_gives_zero_messages(self) -> None:
        """Test mapping with no message pieces."""
        ar = _make_attack_result()
        stats = ConversationStats(message_count=0)

        summary = await attack_result_to_summary_async(ar, stats=stats)

        assert summary.message_count == 0
        assert summary.last_message_preview is None

    async def test_last_message_preview_truncates_long_raw_text(self) -> None:
        """The mapper applies the preview formatter, which truncates long raw text."""
        ar = _make_attack_result()
        long_text = "x" * 200
        stats = ConversationStats(message_count=1, last_message_preview=long_text, last_message_data_type="text")

        summary = await attack_result_to_summary_async(ar, stats=stats)

        assert summary.last_message_preview is not None
        assert len(summary.last_message_preview) == 103  # 100 + "..."
        assert summary.last_message_preview.endswith("...")

    @pytest.mark.parametrize(
        ("data_type", "expected"),
        [
            ("image_path", "[Image: 1780010098266691.png]"),
            ("audio_path", "[Audio: 1780010098266691.png]"),
            ("video_path", "[Video: 1780010098266691.png]"),
            ("binary_path", "[File: 1780010098266691.png]"),
        ],
    )
    async def test_media_last_message_preview_hides_absolute_path(self, data_type: str, expected: str) -> None:
        """The mapper renders media-type previews as friendly labels rather
        than leaking the raw on-disk path it receives from memory."""
        ar = _make_attack_result()
        path = r"C:\Users\someone\git\PyRIT\dbdata\prompt-memory-entries\media\1780010098266691.png"
        stats = ConversationStats(
            message_count=1,
            last_message_preview=path,
            last_message_data_type=data_type,
        )

        summary = await attack_result_to_summary_async(ar, stats=stats)

        assert summary.last_message_preview == expected
        assert "C:\\" not in (summary.last_message_preview or "")

    async def test_labels_are_mapped(self) -> None:
        """Test that labels are derived from stats."""
        ar = _make_attack_result()
        stats = ConversationStats(message_count=1, labels={"env": "prod", "team": "red"})

        summary = await attack_result_to_summary_async(ar, stats=stats)

        assert summary.labels == {"env": "prod", "team": "red", "test_ar_label": "test_ar_value"}

    async def test_labels_passed_through_without_normalization(self) -> None:
        """Test that labels are passed through as-is (DB stores canonical keys after migration)."""
        ar = _make_attack_result()
        stats = ConversationStats(
            message_count=1,
            labels={"operator": "alice", "operation": "op_red", "env": "prod"},
        )

        summary = await attack_result_to_summary_async(ar, stats=stats)

        assert summary.labels == {
            "operator": "alice",
            "operation": "op_red",
            "env": "prod",
            "test_ar_label": "test_ar_value",
        }

    async def test_conversation_labels_take_precedence_on_collision(self) -> None:
        """Test that conversation-level labels override attack-result labels on key collision."""
        ar = _make_attack_result()
        stats = ConversationStats(
            message_count=1,
            labels={"test_ar_label": "conversation_wins"},
        )

        summary = await attack_result_to_summary_async(ar, stats=stats)

        assert summary.labels["test_ar_label"] == "conversation_wins"

    async def test_outcome_success(self) -> None:
        """Test that success outcome is mapped."""
        ar = _make_attack_result(outcome=AttackOutcome.SUCCESS)
        stats = ConversationStats(message_count=0)

        summary = await attack_result_to_summary_async(ar, stats=stats)

        assert summary.outcome == "success"

    async def test_no_target_returns_none_fields(self) -> None:
        """Test that target fields are None when no target identifier exists."""
        ar = _make_attack_result(has_target=False)
        stats = ConversationStats(message_count=0)

        summary = await attack_result_to_summary_async(ar, stats=stats)

        assert summary.target is None

    async def test_attack_specific_params_passed_through(self) -> None:
        """Test that attack_specific_params are extracted from identifier."""
        ar = _make_attack_result()
        stats = ConversationStats(message_count=0)

        summary = await attack_result_to_summary_async(ar, stats=stats)

        assert summary.attack_specific_params == {"source": "gui"}

    async def test_converters_extracted_from_identifier(self) -> None:
        """Test that converter class names are extracted into converters list."""
        now = datetime.now(timezone.utc)
        ar = AttackResult(
            conversation_id="attack-conv",
            objective="test",
            attack_result_id=str(uuid.uuid4()),
            atomic_attack_identifier=AtomicAttackIdentifier.build(
                attack_identifier=ComponentIdentifier(
                    class_name="TestAttack",
                    class_module="pyrit.backend",
                    children={
                        "request_converters": [
                            ComponentIdentifier(
                                class_name="Base64Converter",
                                class_module="pyrit.converters",
                                params={
                                    "supported_input_types": ("text",),
                                    "supported_output_types": ("text",),
                                },
                            ),
                            ComponentIdentifier(
                                class_name="ROT13Converter",
                                class_module="pyrit.converters",
                                params={
                                    "supported_input_types": ("text",),
                                    "supported_output_types": ("text",),
                                },
                            ),
                        ],
                    },
                )
            ),
            outcome=AttackOutcome.UNDETERMINED,
            metadata={"created_at": now.isoformat(), "updated_at": now.isoformat()},
            labels={"test_label": "test_value"},
        )

        summary = await attack_result_to_summary_async(ar, stats=ConversationStats(message_count=0))

        assert summary.converters == ["Base64Converter", "ROT13Converter"]

    async def test_no_converters_returns_empty_list(self) -> None:
        """Test that converters is empty list when no converters in identifier."""
        ar = _make_attack_result()
        stats = ConversationStats(message_count=0)

        summary = await attack_result_to_summary_async(ar, stats=stats)

        assert summary.converters == []

    async def test_related_conversation_ids_from_related_conversations(self) -> None:
        """Test that related_conversation_ids includes all related conversation IDs."""
        from pyrit.models import ConversationReference, ConversationType

        ar = _make_attack_result()
        ar.related_conversations = {
            ConversationReference(
                conversation_id="branch-1",
                conversation_type=ConversationType.ADVERSARIAL,
            ),
            ConversationReference(
                conversation_id="pruned-1",
                conversation_type=ConversationType.PRUNED,
            ),
        }

        summary = await attack_result_to_summary_async(ar, stats=ConversationStats(message_count=0))

        assert sorted(summary.related_conversation_ids) == ["branch-1", "pruned-1"]

    async def test_related_conversation_ids_empty_when_no_related(self) -> None:
        """Test that related_conversation_ids is empty when no related conversations exist."""
        ar = _make_attack_result()
        stats = ConversationStats(message_count=0)

        summary = await attack_result_to_summary_async(ar, stats=stats)

        assert summary.related_conversation_ids == []

    async def test_message_count_from_stats(self) -> None:
        """Test that message_count comes from stats."""
        ar = _make_attack_result()
        stats = ConversationStats(message_count=5)

        summary = await attack_result_to_summary_async(ar, stats=stats)

        assert summary.message_count == 5

    async def test_created_at_prefers_ar_timestamp_when_metadata_absent(self) -> None:
        """When metadata['created_at'] is absent but ar.timestamp is set, use ar.timestamp."""
        persisted_ts = datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc)
        ar = AttackResult(
            conversation_id="attack-1",
            objective="test",
            outcome=AttackOutcome.SUCCESS,
            timestamp=persisted_ts,
        )
        summary = await attack_result_to_summary_async(ar, stats=ConversationStats(message_count=0))

        assert summary.created_at == persisted_ts
        assert summary.updated_at == persisted_ts

    async def test_created_at_metadata_still_wins_over_ar_timestamp(self) -> None:
        """When both metadata['created_at'] and ar.timestamp are set, metadata wins (backward compat)."""
        metadata_ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        ar_ts = datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc)
        ar = AttackResult(
            conversation_id="attack-1",
            objective="test",
            outcome=AttackOutcome.SUCCESS,
            timestamp=ar_ts,
            metadata={"created_at": metadata_ts.isoformat()},
        )
        summary = await attack_result_to_summary_async(ar, stats=ConversationStats(message_count=0))

        assert summary.created_at == metadata_ts

    async def test_created_at_falls_back_to_now_when_both_absent(self) -> None:
        """When neither metadata nor ar.timestamp is set, fall back to datetime.now()."""
        ar = AttackResult(
            conversation_id="attack-1",
            objective="test",
            outcome=AttackOutcome.SUCCESS,
        )
        ar.timestamp = None  # type: ignore[assignment]

        before = datetime.now(timezone.utc)
        summary = await attack_result_to_summary_async(ar, stats=ConversationStats(message_count=0))
        after = datetime.now(timezone.utc)

        assert before <= summary.created_at <= after

    async def test_retry_events_mapped_to_response(self) -> None:
        """Test that retry events on an AttackResult are inherited by the AttackSummary."""
        from pyrit.models.retry_event import RetryEvent

        now = datetime.now(timezone.utc)
        ar = _make_attack_result()
        ar.retry_events = [
            RetryEvent(
                timestamp=now,
                attempt_number=1,
                function_name="send_prompt_async",
                exception_type="RateLimitError",
                exception_message="rate limit exceeded",
                component_role="objective_target",
                component_name="OpenAIChatTarget",
                endpoint="https://api.openai.com",
                elapsed_seconds=1.5,
            ),
        ]
        ar.total_retries = 1

        stats = ConversationStats(message_count=0)
        summary = await attack_result_to_summary_async(ar, stats=stats)

        assert summary.retry_events is not None
        assert len(summary.retry_events) == 1
        evt = summary.retry_events[0]
        assert evt.attempt_number == 1
        assert evt.function_name == "send_prompt_async"
        assert evt.exception_type == "RateLimitError"
        assert evt.component_role == "objective_target"
        assert evt.elapsed_seconds == 1.5
        assert summary.total_retries == 1

    """Tests for retry-event passthrough on AttackSummary."""

    def test_maps_scores(self) -> None:
        """Test that a domain score is exposed as a ScoreView with a flattened scorer_type."""
        score = _make_score()

        view = ScoreView.from_domain(score)

        assert view.id == score.id
        assert view.scorer_type == "TrueFalseScorer"
        assert view.score_value == "1.0"
        assert view.score_type == "float_scale"
        assert view.score_rationale == "Looks correct"

    def test_scorer_type_unknown_without_identifier(self) -> None:
        """Test that scorer_type falls back to 'Unknown' when no identifier is set."""
        score = Score(score_value="0.5", score_type="float_scale", message_piece_id=str(uuid.uuid4()))

        view = ScoreView.from_domain(score)

        assert view.scorer_type == "Unknown"

    def test_true_false_scores_are_included(self) -> None:
        """Test that true_false score values and categories are preserved."""
        float_score = _make_score()
        bool_score = _make_score(score_value="false", score_type="true_false", score_category=["hate"])

        result = [ScoreView.from_domain(float_score), ScoreView.from_domain(bool_score)]

        assert len(result) == 2
        assert result[0].score_value == "1.0"
        assert result[1].score_value == "false"
        assert result[1].score_type == "true_false"
        assert result[1].score_category == ["hate"]


class TestPyritMessagesToDto:
    """Tests for pyrit_messages_to_dto_async function."""

    @pytest.fixture(autouse=True)
    def _stub_central_memory(self):
        """Stub CentralMemory so the mapper's score lookup is a no-op for mock-based tests."""
        from pyrit.memory import CentralMemory

        stub = MagicMock()
        stub.get_prompt_scores = MagicMock(return_value=[])
        with patch.object(CentralMemory, "get_memory_instance", return_value=stub):
            yield stub

    async def test_maps_single_message(self) -> None:
        """Test mapping a single message with one piece."""
        piece = _make_piece(original_value="hi", converted_value="hi")
        msg = Message(message_pieces=[piece])

        result = await pyrit_messages_to_dto_async([msg])

        assert len(result) == 1
        assert result[0].role == "user"
        assert len(result[0].message_pieces) == 1
        assert result[0].message_pieces[0].original_value == "hi"
        assert result[0].message_pieces[0].converted_value == "hi"

    async def test_maps_data_types_separately(self) -> None:
        """Test that original and converted data types are mapped independently."""
        piece = _make_piece(
            original_value="describe this",
            converted_value="base64data",
            original_value_data_type="text",
            converted_value_data_type="image_path",
        )
        msg = Message(message_pieces=[piece])

        result = await pyrit_messages_to_dto_async([msg])

        assert result[0].message_pieces[0].original_value_data_type == "text"
        assert result[0].message_pieces[0].converted_value_data_type == "image_path"

    async def test_maps_empty_list(self) -> None:
        """Test mapping an empty messages list."""
        result = await pyrit_messages_to_dto_async([])
        assert result == []

    async def test_populates_mime_type_for_image(self) -> None:
        """Test that MIME types are inferred for image pieces."""
        piece = _make_piece(
            original_value="/path/to/photo.png",
            converted_value="/path/to/photo.jpg",
            original_value_data_type="image_path",
            converted_value_data_type="image_path",
        )
        msg = Message(message_pieces=[piece])

        result = await pyrit_messages_to_dto_async([msg])

        assert result[0].message_pieces[0].original_value_mime_type == "image/png"
        assert result[0].message_pieces[0].converted_value_mime_type == "image/jpeg"

    async def test_mime_type_none_for_text(self) -> None:
        """Test that MIME type is None for text pieces."""
        piece = _make_piece(original_value="hello", converted_value="hello")
        msg = Message(message_pieces=[piece])

        result = await pyrit_messages_to_dto_async([msg])

        assert result[0].message_pieces[0].original_value_mime_type is None
        assert result[0].message_pieces[0].converted_value_mime_type is None

    async def test_mime_type_for_audio(self) -> None:
        """Test that MIME types are inferred for audio pieces."""
        piece = _make_piece(
            original_value="/tmp/speech.wav",
            converted_value="/tmp/speech.mp3",
            original_value_data_type="audio_path",
            converted_value_data_type="audio_path",
        )
        msg = Message(message_pieces=[piece])

        result = await pyrit_messages_to_dto_async([msg])

        # Python 3.10 returns "audio/wav", 3.11+ returns "audio/x-wav"
        assert result[0].message_pieces[0].original_value_mime_type in ("audio/wav", "audio/x-wav")
        assert result[0].message_pieces[0].converted_value_mime_type == "audio/mpeg"

    async def test_local_media_file_returns_media_url(self) -> None:
        """Local media files surface a /api/media URL via *_value_url; raw value stays unchanged."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"PNGDATA")
            tmp_path = tmp.name

        try:
            piece = _make_piece(
                original_value=tmp_path,
                converted_value=tmp_path,
                original_value_data_type="image_path",
                converted_value_data_type="image_path",
            )
            msg = Message(message_pieces=[piece])

            result = await pyrit_messages_to_dto_async([msg])

            view = result[0].message_pieces[0]
            # Raw stored value (inherited from MessagePiece) — unchanged
            assert view.original_value == tmp_path
            assert view.converted_value == tmp_path
            # Client-fetchable URL — populated by the mapper
            assert view.original_value_url is not None
            assert view.original_value_url.startswith("/api/media?path=")
            assert view.converted_value_url is not None
            assert view.converted_value_url.startswith("/api/media?path=")
        finally:
            os.unlink(tmp_path)

    async def test_data_uri_passthrough(self) -> None:
        """Pre-encoded data URIs surface as both the raw value and the URL."""
        piece = _make_piece(
            original_value="data:image/png;base64,AAAA",
            converted_value="data:image/jpeg;base64,BBBB",
            original_value_data_type="image_path",
            converted_value_data_type="image_path",
        )
        msg = Message(message_pieces=[piece])

        result = await pyrit_messages_to_dto_async([msg])

        view = result[0].message_pieces[0]
        assert view.original_value == "data:image/png;base64,AAAA"
        assert view.converted_value == "data:image/jpeg;base64,BBBB"
        assert view.original_value_url == "data:image/png;base64,AAAA"
        assert view.converted_value_url == "data:image/jpeg;base64,BBBB"

    async def test_non_blob_http_url_passthrough(self) -> None:
        """Non-Azure-Blob HTTP URLs surface as both the raw value and the URL."""
        piece = _make_piece(
            original_value="http://example.com/image.png",
            converted_value="http://example.com/image.png",
            original_value_data_type="image_path",
            converted_value_data_type="image_path",
        )
        msg = Message(message_pieces=[piece])

        result = await pyrit_messages_to_dto_async([msg])

        view = result[0].message_pieces[0]
        assert view.original_value == "http://example.com/image.png"
        assert view.converted_value == "http://example.com/image.png"
        assert view.original_value_url == "http://example.com/image.png"
        assert view.converted_value_url == "http://example.com/image.png"

    async def test_azure_blob_url_is_signed(self) -> None:
        """Azure Blob URLs are signed into *_value_url; raw value keeps the unsigned URL."""
        blob_url = "https://myaccount.blob.core.windows.net/dbdata/prompt-memory-entries/images/test.png"
        signed_url = blob_url + "?sig=abc123"
        piece = _make_piece(
            original_value=blob_url,
            converted_value=blob_url,
            original_value_data_type="image_path",
            converted_value_data_type="image_path",
        )
        msg = Message(message_pieces=[piece])

        with patch(
            "pyrit.backend.mappers.attack_mappers._sign_blob_url_async",
            new_callable=AsyncMock,
            return_value=signed_url,
        ):
            result = await pyrit_messages_to_dto_async([msg])

        view = result[0].message_pieces[0]
        # Raw blob URL — unsigned, as stored
        assert view.original_value == blob_url
        assert view.converted_value == blob_url
        # Signed URL — what the client should fetch
        assert view.original_value_url == signed_url
        assert view.converted_value_url == signed_url

    async def test_azure_blob_url_sign_failure_returns_raw_url(self) -> None:
        """Sign failure falls back to the unsigned blob URL on both raw and *_value_url."""
        blob_url = "https://myaccount.blob.core.windows.net/dbdata/images/test.png"
        piece = _make_piece(
            original_value=blob_url,
            converted_value=blob_url,
            original_value_data_type="image_path",
            converted_value_data_type="image_path",
        )
        msg = Message(message_pieces=[piece])

        with patch(
            "pyrit.backend.mappers.attack_mappers._sign_blob_url_async",
            new_callable=AsyncMock,
            return_value=blob_url,  # falls back to raw URL on failure
        ):
            result = await pyrit_messages_to_dto_async([msg])

        view = result[0].message_pieces[0]
        assert view.original_value == blob_url
        assert view.converted_value == blob_url
        assert view.original_value_url == blob_url
        assert view.converted_value_url == blob_url

    async def test_nonexistent_media_file_returns_raw_path(self) -> None:
        """Non-existent local media paths fall back to the raw path on both fields."""
        piece = _make_piece(
            original_value="/tmp/nonexistent.png",
            converted_value="/tmp/nonexistent.png",
            original_value_data_type="image_path",
            converted_value_data_type="image_path",
        )
        msg = Message(message_pieces=[piece])

        result = await pyrit_messages_to_dto_async([msg])

        view = result[0].message_pieces[0]
        assert view.original_value == "/tmp/nonexistent.png"
        assert view.converted_value == "/tmp/nonexistent.png"
        assert view.original_value_url == "/tmp/nonexistent.png"
        assert view.converted_value_url == "/tmp/nonexistent.png"

    async def test_text_piece_url_fields_are_none(self) -> None:
        """Text pieces don't have a fetchable URL — *_value_url is None."""
        piece = _make_piece(original_value="hello world", converted_value="hello world")
        msg = Message(message_pieces=[piece])

        result = await pyrit_messages_to_dto_async([msg])

        view = result[0].message_pieces[0]
        assert view.original_value == "hello world"
        assert view.converted_value == "hello world"
        assert view.original_value_url is None
        assert view.converted_value_url is None


@pytest.mark.usefixtures("patch_central_database")
class TestPyritMessagesToDtoRealObjects:
    """Regression tests for pyrit_messages_to_dto_async using real domain objects.

    The mock-based ``TestPyritMessagesToDto`` suite above sets ``p.scores = []``
    directly on a ``MagicMock``, which masks the fact that ``MessagePiece`` no
    longer carries a ``scores`` attribute. These tests round-trip real
    ``MessagePiece`` / ``Score`` instances through a real ``SQLiteMemory`` so
    that any regression to ``p.scores``-style access on the piece (or other
    drift between the Pydantic model and the mapper) is caught.
    """

    async def test_scores_are_fetched_from_memory_and_attached(self, sqlite_instance) -> None:
        """A real MessagePiece + Score round-trips through the mapper with scores attached."""
        from pyrit.models import Message as RealPyritMessage
        from pyrit.models import MessagePiece as RealPyritMessagePiece
        from pyrit.models import Score as RealPyritScore

        piece = RealPyritMessagePiece(role="user", original_value="hi", conversation_id="real-conv-scores")
        sqlite_instance.add_message_to_memory(request=RealPyritMessage(message_pieces=[piece]))

        score = RealPyritScore(
            score_value="0.75",
            score_type="float_scale",
            score_category=["bias"],
            score_rationale="example rationale",
            message_piece_id=piece.id,
        )
        sqlite_instance.add_scores_to_memory(scores=[score])

        reloaded = sqlite_instance.get_conversation_messages(conversation_id=piece.conversation_id)
        result = await pyrit_messages_to_dto_async(list(reloaded))

        assert len(result) == 1
        dto_pieces = result[0].message_pieces
        assert len(dto_pieces) == 1
        attached = dto_pieces[0].scores
        assert len(attached) == 1
        assert attached[0].score_value == "0.75"
        assert attached[0].score_type == "float_scale"
        assert attached[0].score_category == ["bias"]
        assert attached[0].score_rationale == "example rationale"

    async def test_empty_scores_when_none_recorded(self, sqlite_instance) -> None:
        """A real piece with no scores in memory maps to an empty scores list."""
        from pyrit.models import Message as RealPyritMessage
        from pyrit.models import MessagePiece as RealPyritMessagePiece

        piece = RealPyritMessagePiece(role="user", original_value="hi", conversation_id="real-conv-empty")
        sqlite_instance.add_message_to_memory(request=RealPyritMessage(message_pieces=[piece]))

        reloaded = sqlite_instance.get_conversation_messages(conversation_id=piece.conversation_id)
        result = await pyrit_messages_to_dto_async(list(reloaded))

        assert result[0].message_pieces[0].scores == []

    async def test_scores_are_grouped_per_piece_across_multiple_pieces(self, sqlite_instance) -> None:
        """Scores from a batched fetch are routed to the correct originating piece."""
        from pyrit.models import Message as RealPyritMessage
        from pyrit.models import MessagePiece as RealPyritMessagePiece
        from pyrit.models import Score as RealPyritScore

        conv_id = "real-conv-1"
        user_piece = RealPyritMessagePiece(role="user", original_value="ask", conversation_id=conv_id)
        sqlite_instance.add_message_to_memory(request=RealPyritMessage(message_pieces=[user_piece]))
        assistant_piece = RealPyritMessagePiece(role="assistant", original_value="reply", conversation_id=conv_id)
        sqlite_instance.add_message_to_memory(request=RealPyritMessage(message_pieces=[assistant_piece]))

        sqlite_instance.add_scores_to_memory(
            scores=[
                RealPyritScore(
                    score_value="true",
                    score_type="true_false",
                    score_rationale="refusal detected",
                    message_piece_id=assistant_piece.id,
                ),
                RealPyritScore(
                    score_value="0.1",
                    score_type="float_scale",
                    score_rationale="low severity",
                    message_piece_id=assistant_piece.id,
                ),
            ]
        )

        reloaded = sqlite_instance.get_conversation_messages(conversation_id=conv_id)
        result = await pyrit_messages_to_dto_async(list(reloaded))

        by_role = {msg.role: msg for msg in result}
        assert by_role["user"].message_pieces[0].scores == []
        assistant_scores = by_role["assistant"].message_pieces[0].scores
        assert len(assistant_scores) == 2
        assert {s.score_value for s in assistant_scores} == {"true", "0.1"}


class TestIsAzureBlobUrl:
    """Tests for _is_azure_blob_url helper."""

    def test_azure_blob_url_detected(self) -> None:
        assert _is_azure_blob_url("https://account.blob.core.windows.net/container/blob.png") is True

    def test_http_non_blob_url_not_detected(self) -> None:
        assert _is_azure_blob_url("http://example.com/image.png") is False

    def test_https_non_blob_url_not_detected(self) -> None:
        assert _is_azure_blob_url("https://example.com/image.png") is False

    def test_data_uri_not_detected(self) -> None:
        assert _is_azure_blob_url("data:image/png;base64,AAAA") is False

    def test_local_path_not_detected(self) -> None:
        assert _is_azure_blob_url("/tmp/test.png") is False


class TestSignBlobUrlAsync:
    """Tests for _sign_blob_url_async helper."""

    async def test_non_blob_url_unchanged(self) -> None:
        """Non-Azure URLs pass through without signing."""
        result = await _sign_blob_url_async(blob_url="http://example.com/img.png")
        assert result == "http://example.com/img.png"

    async def test_already_signed_url_is_re_signed(self) -> None:
        """URLs with existing query params (expired SAS) are stripped and re-signed."""
        url = "https://acct.blob.core.windows.net/c/b.png?sv=2024&sig=old"
        with patch(
            "pyrit.backend.mappers.attack_mappers._get_sas_for_container_async",
            new_callable=AsyncMock,
            return_value="sv=2024&sig=fresh",
        ):
            result = await _sign_blob_url_async(blob_url=url)
        assert result == "https://acct.blob.core.windows.net/c/b.png?sv=2024&sig=fresh"

    async def test_appends_sas_token(self) -> None:
        """SAS token is appended to unsigned blob URLs."""
        url = "https://acct.blob.core.windows.net/container/path/blob.png"
        with patch(
            "pyrit.backend.mappers.attack_mappers._get_sas_for_container_async",
            new_callable=AsyncMock,
            return_value="sv=2024&sig=test",
        ) as mock_sas:
            result = await _sign_blob_url_async(blob_url=url)

        assert result == f"{url}?sv=2024&sig=test"
        mock_sas.assert_called_once_with(container_url="https://acct.blob.core.windows.net/container")

    async def test_sas_failure_returns_original(self) -> None:
        """SAS generation failure falls back to the unsigned URL."""
        url = "https://acct.blob.core.windows.net/c/b.png"
        with patch(
            "pyrit.backend.mappers.attack_mappers._get_sas_for_container_async",
            new_callable=AsyncMock,
            side_effect=RuntimeError("auth error"),
        ):
            result = await _sign_blob_url_async(blob_url=url)

        assert result == url

    async def test_empty_path_returns_original(self) -> None:
        """Blob URL with empty path is returned unsigned."""
        url = "https://acct.blob.core.windows.net"
        with patch("pyrit.backend.mappers.attack_mappers._is_azure_blob_url", return_value=True):
            result = await _sign_blob_url_async(blob_url=url)
        assert result == url


class TestResolveMediaUrl:
    """Tests for _resolve_media_url helper."""

    def test_text_value_returns_none(self) -> None:
        """Non-media types have no fetchable URL — return None."""
        assert _resolve_media_url(value="hello world", data_type="text") is None

    def test_text_empty_value_returns_none(self) -> None:
        """Empty values return None even for media data types."""
        assert _resolve_media_url(value="", data_type="image_path") is None

    def test_data_uri_passes_through(self) -> None:
        """Pre-encoded data URIs are returned as-is."""
        uri = "data:image/png;base64,AAAA"
        assert _resolve_media_url(value=uri, data_type="image_path") == uri

    def test_http_url_passes_through(self) -> None:
        """HTTP/HTTPS URLs are returned as-is (signed later)."""
        url = "https://acct.blob.core.windows.net/container/image.png"
        assert _resolve_media_url(value=url, data_type="image_path") == url

    def test_local_file_returns_media_url(self) -> None:
        """Local file paths are converted to /api/media URLs."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"PNG")
            tmp_path = tmp.name

        try:
            result = _resolve_media_url(value=tmp_path, data_type="image_path")
            assert result is not None
            assert result.startswith("/api/media?path=")
        finally:
            os.unlink(tmp_path)

    def test_nonexistent_file_returns_raw_value(self) -> None:
        """Non-existent file paths are returned as-is."""
        assert _resolve_media_url(value="/no/such/file.png", data_type="image_path") == "/no/such/file.png"

    def test_none_value_returns_none(self) -> None:
        """None values are returned as None."""
        assert _resolve_media_url(value=None, data_type="image_path") is None

    def test_works_for_all_path_types(self) -> None:
        """All *_path data types are handled."""
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(b"VIDEO")
            tmp_path = tmp.name

        try:
            for dtype in ("image_path", "audio_path", "video_path", "binary_path"):
                result = _resolve_media_url(value=tmp_path, data_type=dtype)
                assert result is not None
                assert result.startswith("/api/media?path="), f"Failed for {dtype}"
        finally:
            os.unlink(tmp_path)


class TestRequestToPyritMessage:
    """Tests for request_to_pyrit_message function."""

    def test_converts_request_to_domain(self) -> None:
        """Test that DTO request is correctly converted to domain message."""
        request = MagicMock()
        request.role = "user"
        piece = MagicMock()
        piece.data_type = "text"
        piece.original_value = "hello"
        piece.converted_value = None
        piece.original_prompt_id = None
        request.pieces = [piece]

        result = request_to_pyrit_message(
            request=request,
            conversation_id="conv-1",
            sequence=0,
        )

        assert len(result.message_pieces) == 1
        assert result.message_pieces[0].original_value == "hello"
        assert result.message_pieces[0].conversation_id == "conv-1"
        assert result.message_pieces[0].sequence == 0

    def test_labels_emit_deprecation_warning(self) -> None:
        """Test that passing labels emits deprecation warning through mapper helper."""
        request = MagicMock()
        request.role = "user"
        piece = MagicMock()
        piece.data_type = "text"
        piece.original_value = "hello"
        piece.converted_value = None
        piece.prompt_metadata = None
        piece.mime_type = None
        piece.original_prompt_id = None
        request.pieces = [piece]

        with patch("pyrit.backend.mappers.attack_mappers.print_deprecation_message") as mock_deprecation:
            request_to_pyrit_message(
                request=request,
                conversation_id="conv-1",
                sequence=0,
                labels={"env": "prod"},
            )

        assert mock_deprecation.call_count == 2

    def test_empty_labels_no_deprecation_warning(self) -> None:
        """An explicit empty ``labels={}`` (forwarded on the happy path) must not warn."""
        request = MagicMock()
        request.role = "user"
        piece = MagicMock()
        piece.data_type = "text"
        piece.original_value = "hello"
        piece.converted_value = None
        piece.prompt_metadata = None
        piece.mime_type = None
        piece.original_prompt_id = None
        request.pieces = [piece]

        with patch("pyrit.backend.mappers.attack_mappers.print_deprecation_message") as mock_deprecation:
            request_to_pyrit_message(
                request=request,
                conversation_id="conv-1",
                sequence=0,
                labels={},
            )

        mock_deprecation.assert_not_called()


class TestRequestPieceToPyritMessagePiece:
    """Tests for request_piece_to_pyrit_message_piece function."""

    def test_uses_converted_value_when_present(self) -> None:
        """Test that converted_value is used when provided."""
        piece = MagicMock()
        piece.data_type = "text"
        piece.original_value = "original"
        piece.converted_value = "converted"
        piece.prompt_metadata = None
        piece.original_prompt_id = None

        result = request_piece_to_pyrit_message_piece(
            piece=piece,
            role="assistant",
            conversation_id="conv-1",
            sequence=5,
        )

        assert result.original_value == "original"
        assert result.converted_value == "converted"
        assert result.api_role == "assistant"
        assert result.sequence == 5

    def test_falls_back_to_original_when_no_converted(self) -> None:
        """Test that original_value is used when converted_value is None."""
        piece = MagicMock()
        piece.data_type = "text"
        piece.original_value = "fallback"
        piece.converted_value = None
        piece.prompt_metadata = None
        piece.original_prompt_id = None

        result = request_piece_to_pyrit_message_piece(
            piece=piece,
            role="user",
            conversation_id="conv-1",
            sequence=0,
        )

        assert result.converted_value == "fallback"

    def test_passes_mime_type_through_prompt_metadata(self) -> None:
        """Test that mime_type is stored in prompt_metadata."""
        piece = MagicMock()
        piece.data_type = "image_path"
        piece.original_value = "base64data"
        piece.converted_value = None
        piece.mime_type = "image/png"
        piece.prompt_metadata = None
        piece.original_prompt_id = None

        result = request_piece_to_pyrit_message_piece(
            piece=piece,
            role="user",
            conversation_id="conv-1",
            sequence=0,
        )

        assert result.prompt_metadata == {"mime_type": "image/png"}

    def test_prompt_metadata_takes_precedence_over_mime_type(self) -> None:
        """Test that prompt_metadata is used when provided, ignoring mime_type."""
        piece = MagicMock()
        piece.data_type = "video_path"
        piece.original_value = "base64data"
        piece.converted_value = None
        piece.prompt_metadata = {"video_id": "abc-123"}
        piece.mime_type = "video/mp4"
        piece.original_prompt_id = None

        result = request_piece_to_pyrit_message_piece(
            piece=piece,
            role="user",
            conversation_id="conv-1",
            sequence=0,
        )

        assert result.prompt_metadata == {"video_id": "abc-123"}

    def test_no_metadata_when_mime_type_absent(self) -> None:
        """Test that prompt_metadata is empty when mime_type is None."""
        piece = MagicMock()
        piece.data_type = "text"
        piece.original_value = "hello"
        piece.converted_value = None
        piece.mime_type = None
        piece.prompt_metadata = None
        piece.original_prompt_id = None

        result = request_piece_to_pyrit_message_piece(
            piece=piece,
            role="user",
            conversation_id="conv-1",
            sequence=0,
        )

        assert result.prompt_metadata == {}

    def test_labels_are_stamped_on_piece(self) -> None:
        """Test that labels are passed through to the MessagePiece."""
        piece = MagicMock()
        piece.data_type = "text"
        piece.original_value = "hello"
        piece.converted_value = None
        piece.mime_type = None
        piece.prompt_metadata = None
        piece.original_prompt_id = None

        result = request_piece_to_pyrit_message_piece(
            piece=piece,
            role="user",
            conversation_id="conv-1",
            sequence=0,
            labels={"env": "prod"},
        )

        assert result.labels == {"env": "prod"}

    def test_labels_emit_deprecation_warning(self) -> None:
        """Test that passing labels emits deprecation warning."""
        piece = MagicMock()
        piece.data_type = "text"
        piece.original_value = "hello"
        piece.converted_value = None
        piece.mime_type = None
        piece.prompt_metadata = None
        piece.original_prompt_id = None

        with patch("pyrit.backend.mappers.attack_mappers.print_deprecation_message") as mock_deprecation:
            request_piece_to_pyrit_message_piece(
                piece=piece,
                role="user",
                conversation_id="conv-1",
                sequence=0,
                labels={"env": "prod"},
            )

        mock_deprecation.assert_called_once()

    def test_empty_labels_no_deprecation_warning(self) -> None:
        """An explicit empty ``labels={}`` (forwarded on the happy path) must not warn."""
        piece = MagicMock()
        piece.data_type = "text"
        piece.original_value = "hello"
        piece.converted_value = None
        piece.mime_type = None
        piece.prompt_metadata = None
        piece.original_prompt_id = None

        with patch("pyrit.backend.mappers.attack_mappers.print_deprecation_message") as mock_deprecation:
            request_piece_to_pyrit_message_piece(
                piece=piece,
                role="user",
                conversation_id="conv-1",
                sequence=0,
                labels={},
            )

        mock_deprecation.assert_not_called()

    def test_labels_default_to_empty_dict(self) -> None:
        """Test that labels default to empty dict when not provided."""
        piece = MagicMock()
        piece.data_type = "text"
        piece.original_value = "hello"
        piece.converted_value = None
        piece.mime_type = None
        piece.prompt_metadata = None
        piece.original_prompt_id = None

        result = request_piece_to_pyrit_message_piece(
            piece=piece,
            role="user",
            conversation_id="conv-1",
            sequence=0,
        )

        assert result.labels == {}

    def test_original_prompt_id_forwarded_when_provided(self) -> None:
        """Test that original_prompt_id is passed through for lineage tracking."""
        piece = MagicMock()
        piece.data_type = "text"
        piece.original_value = "hello"
        piece.converted_value = None
        piece.mime_type = None
        piece.original_prompt_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        result = request_piece_to_pyrit_message_piece(
            piece=piece,
            role="user",
            conversation_id="conv-1",
            sequence=0,
        )

        assert result.original_prompt_id == uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        # New piece should have its own id, different from original_prompt_id
        assert result.id != result.original_prompt_id

    def test_original_prompt_id_defaults_to_self_when_absent(self) -> None:
        """Test that original_prompt_id defaults to the piece's own id when not provided."""
        piece = MagicMock()
        piece.data_type = "text"
        piece.original_value = "hello"
        piece.converted_value = None
        piece.mime_type = None
        piece.original_prompt_id = None

        result = request_piece_to_pyrit_message_piece(
            piece=piece,
            role="user",
            conversation_id="conv-1",
            sequence=0,
        )

        assert result.original_prompt_id == result.id


class TestInferMimeType:
    """Tests for infer_mime_type helper function."""

    def test_returns_none_for_text(self) -> None:
        """Text data type should always return None."""
        assert infer_mime_type(value="/path/to/file.png", data_type="text") is None

    def test_returns_none_for_empty_value(self) -> None:
        """Empty or None value should return None."""
        assert infer_mime_type(value=None, data_type="image_path") is None
        assert infer_mime_type(value="", data_type="image_path") is None

    def test_infers_png(self) -> None:
        """Test MIME type inference for PNG files."""
        assert infer_mime_type(value="/tmp/photo.png", data_type="image_path") == "image/png"

    def test_infers_jpeg(self) -> None:
        """Test MIME type inference for JPEG files."""
        assert infer_mime_type(value="/tmp/photo.jpg", data_type="image_path") == "image/jpeg"

    def test_infers_wav(self) -> None:
        """Test MIME type inference for WAV files."""
        result = infer_mime_type(value="/tmp/audio.wav", data_type="audio_path")
        assert result is not None
        assert "wav" in result

    def test_infers_mp3(self) -> None:
        """Test MIME type inference for MP3 files."""
        assert infer_mime_type(value="/tmp/audio.mp3", data_type="audio_path") == "audio/mpeg"

    def test_returns_none_for_unknown_extension(self) -> None:
        """Test that unrecognized extensions return None."""
        assert infer_mime_type(value="/tmp/data.xyz123", data_type="image_path") is None

    def test_infers_mp4(self) -> None:
        """Test MIME type inference for MP4 video files."""
        assert infer_mime_type(value="/tmp/video.mp4", data_type="video_path") == "video/mp4"


class TestBuildFilename:
    """Tests for build_filename helper function."""

    def test_image_path_with_hash(self) -> None:
        result = build_filename(data_type="image_path", sha256="abcdef1234567890", value="/tmp/photo.png")
        assert result == "image_abcdef123456.png"

    def test_audio_path_with_hash(self) -> None:
        result = build_filename(data_type="audio_path", sha256="1234abcd5678efgh", value="/tmp/speech.wav")
        assert result == "audio_1234abcd5678.wav"

    def test_video_path_with_hash(self) -> None:
        result = build_filename(data_type="video_path", sha256="deadbeef00000000", value="/tmp/clip.mp4")
        assert result == "video_deadbeef0000.mp4"

    def test_binary_path_with_hash(self) -> None:
        result = build_filename(data_type="binary_path", sha256="cafe0123babe4567", value="/tmp/doc.pdf")
        assert result == "file_cafe0123babe.pdf"

    def test_returns_none_for_text(self) -> None:
        assert build_filename(data_type="text", sha256="abc123", value="hello") is None

    def test_returns_none_for_reasoning(self) -> None:
        assert build_filename(data_type="reasoning", sha256="abc123", value="thinking") is None

    def test_fallback_ext_when_no_value(self) -> None:
        result = build_filename(data_type="image_path", sha256="abcdef1234567890", value=None)
        assert result == "image_abcdef123456.png"

    def test_fallback_ext_for_data_uri(self) -> None:
        result = build_filename(data_type="audio_path", sha256="abcdef1234567890", value="data:audio/wav;base64,AAA=")
        assert result == "audio_abcdef123456.wav"

    def test_random_hash_when_no_sha256(self) -> None:
        result = build_filename(data_type="image_path", sha256=None, value="/tmp/photo.png")
        assert result is not None
        assert result.startswith("image_")
        assert result.endswith(".png")
        assert len(result) == len("image_123456789012.png")

    def test_blob_url_extension(self) -> None:
        url = "https://account.blob.core.windows.net/container/images/photo.jpg"
        result = build_filename(data_type="image_path", sha256="abcdef1234567890", value=url)
        assert result == "image_abcdef123456.jpg"


# ============================================================================
# Target Mapper Tests
# ============================================================================


class TestTargetObjectToInstance:
    """Tests for target_object_to_instance function."""

    def test_maps_target_with_identifier(self) -> None:
        """Test mapping a target object that has get_identifier."""
        target_obj = MagicMock()
        mock_identifier = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={
                "endpoint": "http://test",
                "model_name": "gpt-4",
                "temperature": 0.7,
            },
        )
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.target_registry_name == "t-1"
        assert result.target_type == "OpenAIChatTarget"
        assert result.endpoint == "http://test"
        assert result.model_name == "gpt-4"
        assert result.temperature == 0.7
        # identifier_hash is auto-populated by the ComponentIdentifier validator and
        # surfaced on the DTO so the frontend can dedupe targets that resolve to the
        # same underlying configuration.
        assert result.identifier_hash is not None
        assert result.identifier_hash == mock_identifier.hash

    def test_no_endpoint_returns_none(self) -> None:
        """Test that missing endpoint returns None."""
        target_obj = MagicMock()
        mock_identifier = ComponentIdentifier(
            class_name="TextTarget",
            class_module="pyrit.prompt_target",
        )
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.target_type == "TextTarget"
        assert result.endpoint is None
        assert result.model_name is None

    def test_no_get_identifier_uses_class_name(self) -> None:
        """Test that target uses class name from identifier."""
        target_obj = MagicMock()
        mock_identifier = ComponentIdentifier(class_name="FakeTarget", class_module="pyrit.prompt_target")
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.target_type == "FakeTarget"
        assert result.endpoint is None
        assert result.model_name is None

    def test_supports_multi_turn_true_when_capability_set(self) -> None:
        """Test that targets with supports_multi_turn capability expose it via capabilities."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities(supports_multi_turn=True)
        mock_identifier = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={
                "endpoint": "https://api.openai.com",
                "model_name": "gpt-4",
            },
        )
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.capabilities.supports_multi_turn is True

    def test_supports_multi_turn_false_when_capability_not_set(self) -> None:
        """Test that targets without supports_multi_turn capability expose False via capabilities."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities(supports_multi_turn=False)
        mock_identifier = ComponentIdentifier(
            class_name="TextTarget",
            class_module="pyrit.prompt_target",
        )
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.capabilities.supports_multi_turn is False

    def test_supports_multi_turn_not_extracted_from_identifier_params(self) -> None:
        """Identifier-level supports_multi_turn must not leak into target_specific_params or override capabilities."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities(supports_multi_turn=True)
        mock_identifier = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={
                "endpoint": "https://api.openai.com",
                "model_name": "gpt-4",
                "supports_multi_turn": False,
            },
        )
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.capabilities.supports_multi_turn is True
        # supports_multi_turn from identifier params should NOT bleed into target_specific_params
        assert result.target_specific_params is None or "supports_multi_turn" not in result.target_specific_params

    def test_capabilities_includes_all_capability_flags(self) -> None:
        """Test that all boolean capability flags are exposed via the capabilities DTO."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities(
            supports_multi_turn=True,
            supports_multi_message_pieces=True,
            supports_json_schema=True,
            supports_json_output=True,
            supports_editable_history=True,
            supports_system_prompt=True,
        )
        mock_identifier = ComponentIdentifier(class_name="FullCapTarget", class_module="pyrit.prompt_target")
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.capabilities.supports_multi_turn is True
        assert result.capabilities.supports_multi_message_pieces is True
        assert result.capabilities.supports_json_schema is True
        assert result.capabilities.supports_json_output is True
        assert result.capabilities.supports_editable_history is True
        assert result.capabilities.supports_system_prompt is True

    def test_capabilities_defaults_when_capabilities_minimal(self) -> None:
        """Test that unset capability flags default to False."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities()
        mock_identifier = ComponentIdentifier(class_name="MinimalTarget", class_module="pyrit.prompt_target")
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.capabilities.supports_multi_turn is False
        assert result.capabilities.supports_multi_message_pieces is False
        assert result.capabilities.supports_json_schema is False
        assert result.capabilities.supports_json_output is False
        assert result.capabilities.supports_editable_history is False
        assert result.capabilities.supports_system_prompt is False

    def test_extra_params_in_target_specific_params(self) -> None:
        """Test that non-extracted params like reasoning_effort appear in target_specific_params."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities(supports_multi_turn=True)
        mock_identifier = ComponentIdentifier(
            class_name="OpenAIResponseTarget",
            class_module="pyrit.prompt_target",
            params={
                "endpoint": "https://api.openai.com",
                "model_name": "o3",
                "temperature": 1.0,
                "reasoning_effort": "high",
                "reasoning_summary": "auto",
                "max_output_tokens": 4096,
            },
        )
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.temperature == 1.0
        assert result.target_specific_params is not None
        assert result.target_specific_params["reasoning_effort"] == "high"
        assert result.target_specific_params["reasoning_summary"] == "auto"
        assert result.target_specific_params["max_output_tokens"] == 4096

    def test_no_extra_params_returns_none_target_specific(self) -> None:
        """Test that when only extracted params exist, target_specific_params is None."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities(supports_multi_turn=True)
        mock_identifier = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={
                "endpoint": "https://api.openai.com",
                "model_name": "gpt-4",
                "temperature": 0.7,
                "top_p": 0.9,
            },
        )
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.temperature == 0.7
        assert result.top_p == 0.9
        assert result.target_specific_params is None

    def test_none_valued_extra_params_excluded(self) -> None:
        """Test that extra params with None values are excluded from target_specific_params."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities(supports_multi_turn=True)
        mock_identifier = ComponentIdentifier(
            class_name="OpenAIResponseTarget",
            class_module="pyrit.prompt_target",
            params={
                "endpoint": "https://api.openai.com",
                "model_name": "o3",
                "reasoning_effort": "high",
                "reasoning_summary": None,
                "max_output_tokens": None,
            },
        )
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.target_specific_params is not None
        assert result.target_specific_params == {"reasoning_effort": "high"}

    def test_explicit_target_specific_params_merged_with_extras(self) -> None:
        """Test that explicit target_specific_params from identifier merge with extra params."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities(supports_multi_turn=True)
        mock_identifier = ComponentIdentifier(
            class_name="CustomTarget",
            class_module="pyrit.prompt_target",
            params={
                "endpoint": "https://custom.api",
                "model_name": "custom-model",
                "extra_body_parameters": {"custom_key": "custom_val"},
                "target_specific_params": {"explicit_param": "explicit_val"},
            },
        )
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.target_specific_params is not None
        # extra_body_parameters is an extra param (not in extracted keys)
        assert result.target_specific_params["extra_body_parameters"] == {"custom_key": "custom_val"}
        # explicit target_specific_params from identifier should also be present
        assert result.target_specific_params["explicit_param"] == "explicit_val"

    def test_chat_target_extra_params_preserved(self) -> None:
        """Test that OpenAIChatTarget params like frequency_penalty appear in target_specific_params."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities(supports_multi_turn=True)
        mock_identifier = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={
                "endpoint": "https://api.openai.com",
                "model_name": "gpt-4",
                "temperature": 0.7,
                "top_p": 0.9,
                "frequency_penalty": 0.5,
                "presence_penalty": 0.3,
                "seed": 42,
                "max_completion_tokens": 2048,
            },
        )
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.temperature == 0.7
        assert result.top_p == 0.9
        assert result.target_specific_params is not None
        assert result.target_specific_params["frequency_penalty"] == 0.5
        assert result.target_specific_params["presence_penalty"] == 0.3
        assert result.target_specific_params["seed"] == 42
        assert result.target_specific_params["max_completion_tokens"] == 2048

    def test_supported_input_modalities_text_only_default(self) -> None:
        """Test that a target with default capabilities reports only 'text'."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities()
        mock_identifier = ComponentIdentifier(class_name="TextTarget", class_module="pyrit.prompt_target")
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.capabilities.supported_input_modalities == ["text"]

    def test_supported_input_modalities_multimodal(self) -> None:
        """Test that a multimodal target reports all individual input types."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities(
            input_modalities=frozenset(
                {
                    frozenset({"text"}),
                    frozenset({"image_path"}),
                    frozenset({"text", "image_path"}),
                }
            ),
        )
        mock_identifier = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
        )
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.capabilities.supported_input_modalities == ["image_path", "text"]

    def test_supported_input_modalities_audio_video(self) -> None:
        """Test that a target supporting audio and video reports those types."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities(
            input_modalities=frozenset(
                {
                    frozenset({"text"}),
                    frozenset({"audio_path"}),
                    frozenset({"image_path"}),
                    frozenset({"text", "audio_path", "image_path"}),
                }
            ),
        )
        mock_identifier = ComponentIdentifier(class_name="RealtimeTarget", class_module="pyrit.prompt_target")
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.capabilities.supported_input_modalities == ["audio_path", "image_path", "text"]

    def test_supported_output_modalities_default_text(self) -> None:
        """Test that a target with default capabilities reports only 'text' as output."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities()
        mock_identifier = ComponentIdentifier(class_name="TextTarget", class_module="pyrit.prompt_target")
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.capabilities.supported_output_modalities == ["text"]

    def test_supported_output_modalities_image_target(self) -> None:
        """Test that an image-output target reports 'image_path' in supported_output_modalities."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities(
            output_modalities=frozenset({frozenset({"image_path"})}),
        )
        mock_identifier = ComponentIdentifier(class_name="OpenAIImageTarget", class_module="pyrit.prompt_target")
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.capabilities.supported_output_modalities == ["image_path"]

    def test_supported_output_modalities_video_with_audio(self) -> None:
        """Test that a video target reports flattened sorted unique output modalities."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities(
            output_modalities=frozenset(
                {
                    frozenset({"audio_path", "video_path"}),
                    frozenset({"video_path"}),
                }
            ),
        )
        mock_identifier = ComponentIdentifier(class_name="SoraTarget", class_module="pyrit.prompt_target")
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.capabilities.supported_output_modalities == ["audio_path", "video_path"]

    def test_target_configuration_excluded_from_target_specific_params(self) -> None:
        """Test that the verbose target_configuration blob is filtered from target_specific_params."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities(supports_multi_turn=True)
        mock_identifier = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={
                "endpoint": "https://api.openai.com",
                "model_name": "gpt-4",
                "target_configuration": {"capabilities": {"supports_multi_turn": True}},
                "reasoning_effort": "high",
            },
        )
        target_obj.get_identifier.return_value = mock_identifier

        result = target_object_to_instance("t-1", target_obj)

        assert result.target_specific_params is not None
        assert "target_configuration" not in result.target_specific_params
        assert result.target_specific_params["reasoning_effort"] == "high"


class TestTargetObjectToInstanceRoundRobin:
    """Tests for target_object_to_instance with RoundRobinTarget."""

    def test_round_robin_populates_inner_targets(self) -> None:
        """Inner targets list is populated for RoundRobinTarget objects."""
        from pyrit.prompt_target.round_robin_target import RoundRobinTarget

        # Build a mock RoundRobinTarget with two inner targets
        rr = MagicMock(spec=RoundRobinTarget)
        rr.capabilities = TargetCapabilities(supports_multi_turn=True)

        inner_a = MagicMock(spec=PromptTarget)
        inner_a.capabilities = TargetCapabilities()
        inner_a.get_identifier.return_value = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"endpoint": "https://a.openai.azure.com", "model_name": "gpt-4o"},
        )

        inner_b = MagicMock(spec=PromptTarget)
        inner_b.capabilities = TargetCapabilities()
        inner_b.get_identifier.return_value = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"endpoint": "https://b.openai.azure.com", "model_name": "gpt-4o"},
        )

        rr._targets = [inner_a, inner_b]
        rr.get_identifier.return_value = ComponentIdentifier(
            class_name="RoundRobinTarget",
            class_module="pyrit.prompt_target.round_robin_target",
            params={"weights": [1, 1]},
        )

        result = target_object_to_instance("rr-1", rr)

        assert result.target_type == "RoundRobinTarget"
        assert result.inner_targets is not None
        assert len(result.inner_targets) == 2
        assert result.inner_targets[0].endpoint == "https://a.openai.azure.com"
        assert result.inner_targets[1].endpoint == "https://b.openai.azure.com"

    def test_round_robin_hoists_model_name_when_all_inner_targets_match(self) -> None:
        """model_name is hoisted only when all inner targets share the same deployment name."""
        from pyrit.prompt_target.round_robin_target import RoundRobinTarget

        rr = MagicMock(spec=RoundRobinTarget)
        rr.capabilities = TargetCapabilities()

        inner_a = MagicMock(spec=PromptTarget)
        inner_a.capabilities = TargetCapabilities()
        inner_a.get_identifier.return_value = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"model_name": "gpt-4o", "underlying_model_name": "gpt-4o"},
        )

        inner_b = MagicMock(spec=PromptTarget)
        inner_b.capabilities = TargetCapabilities()
        inner_b.get_identifier.return_value = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"model_name": "gpt-4o", "underlying_model_name": "gpt-4o"},
        )

        rr._targets = [inner_a, inner_b]
        rr.get_identifier.return_value = ComponentIdentifier(
            class_name="RoundRobinTarget",
            class_module="pyrit.prompt_target.round_robin_target",
            params={"weights": [1, 1]},
        )

        result = target_object_to_instance("rr-2", rr)

        assert result.model_name == "gpt-4o"
        assert result.underlying_model_name == "gpt-4o"

    def test_round_robin_omits_model_name_when_inner_targets_differ(self) -> None:
        """model_name is None when inner targets have different deployment names."""
        from pyrit.prompt_target.round_robin_target import RoundRobinTarget

        rr = MagicMock(spec=RoundRobinTarget)
        rr.capabilities = TargetCapabilities()

        inner_a = MagicMock(spec=PromptTarget)
        inner_a.capabilities = TargetCapabilities()
        inner_a.get_identifier.return_value = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"model_name": "deploy-japan", "underlying_model_name": "gpt-4o"},
        )

        inner_b = MagicMock(spec=PromptTarget)
        inner_b.capabilities = TargetCapabilities()
        inner_b.get_identifier.return_value = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={"model_name": "deploy-us", "underlying_model_name": "gpt-4o"},
        )

        rr._targets = [inner_a, inner_b]
        rr.get_identifier.return_value = ComponentIdentifier(
            class_name="RoundRobinTarget",
            class_module="pyrit.prompt_target.round_robin_target",
            params={"weights": [1, 1]},
        )

        result = target_object_to_instance("rr-3", rr)

        # model_name should be None since deployments differ
        assert result.model_name is None
        # underlying_model_name should still be hoisted (they all share gpt-4o)
        assert result.underlying_model_name == "gpt-4o"

    def test_non_round_robin_has_no_inner_targets(self) -> None:
        """Regular targets return None for inner_targets."""
        target_obj = MagicMock(spec=PromptTarget)
        target_obj.capabilities = TargetCapabilities()
        target_obj.get_identifier.return_value = ComponentIdentifier(
            class_name="TextTarget",
            class_module="pyrit.prompt_target",
        )

        result = target_object_to_instance("t-1", target_obj)

        assert result.inner_targets is None


# ============================================================================
# Converter Mapper Tests
# ============================================================================


class TestConverterObjectToInstance:
    """Tests for converter_object_to_instance function."""

    def test_maps_converter_with_identifier(self) -> None:
        """Test mapping a converter object."""
        converter_obj = MagicMock()
        identifier = ComponentIdentifier(
            class_name="Base64Converter",
            class_module="pyrit.converters",
            params={
                "supported_input_types": ("text",),
                "supported_output_types": ("text",),
                "param1": "value1",
            },
        )
        converter_obj.get_identifier.return_value = identifier

        result = converter_object_to_instance("c-1", converter_obj)

        assert result.converter_id == "c-1"
        assert result.converter_type == "Base64Converter"
        assert result.display_name is None
        assert result.supported_input_types == ["text"]
        assert result.supported_output_types == ["text"]
        assert result.converter_specific_params == {"param1": "value1"}
        assert result.sub_converter_ids is None

    def test_sub_converter_ids_passed_through(self) -> None:
        """Test that sub_converter_ids are passed through when provided."""
        converter_obj = MagicMock()
        identifier = ComponentIdentifier(
            class_name="PipelineConverter",
            class_module="pyrit.converters",
            params={
                "supported_input_types": ("text",),
                "supported_output_types": ("text",),
            },
        )
        converter_obj.get_identifier.return_value = identifier

        result = converter_object_to_instance("c-1", converter_obj, sub_converter_ids=["sub-1", "sub-2"])

        assert result.sub_converter_ids == ["sub-1", "sub-2"]

    def test_none_input_output_types_returns_empty_lists(self) -> None:
        """Test that None supported types produce empty lists."""
        converter_obj = MagicMock()
        identifier = ComponentIdentifier(
            class_name="CustomConverter",
            class_module="pyrit.converters",
        )
        converter_obj.get_identifier.return_value = identifier

        result = converter_object_to_instance("c-1", converter_obj)

        assert result.supported_input_types == []
        assert result.supported_output_types == []
        assert result.converter_specific_params is None
        assert result.sub_converter_ids is None


# ============================================================================
# Drift Detection Tests – verify mapper-accessed fields exist on domain models
# ============================================================================


class TestDomainModelFieldsExist:
    """Lightweight safety-net: ensure fields the mappers access still exist on the domain dataclasses.

    If a domain model field is renamed or removed, these tests fail immediately –
    before a mapper silently starts returning incorrect data.
    """

    # -- ComponentIdentifier fields used in attack_mappers.py -----------------

    @pytest.mark.parametrize(
        "field_name",
        [
            "class_name",
            "params",
            "children",
        ],
    )
    def test_component_identifier_has_field(self, field_name: str) -> None:
        field_names = set(ComponentIdentifier.model_fields.keys())
        assert field_name in field_names, (
            f"ComponentIdentifier is missing '{field_name}' – mappers depend on this field"
        )
