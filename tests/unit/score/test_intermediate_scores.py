# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import mssql
from sqlalchemy.orm import Session
from unit.mocks import run_memory_session_async

from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import PromptMemoryEntry, ScorableContentEntry, ScoreEntry
from pyrit.models import (
    ChatMessageRole,
    ComponentIdentifier,
    ContentEntryScorable,
    ContentScorable,
    MessagePiece,
    MessageScorable,
    Scorable,
    Score,
    ScoringExpectation,
)
from pyrit.score import (
    AudioTrueFalseScorer,
    FloatScaleScorer,
    FloatScaleThresholdScorer,
    SubStringScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
)
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator


class _FloatScorer(FloatScaleScorer):
    def __init__(self, *, scores: list[Score]) -> None:
        super().__init__()
        self.scores = scores

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        return self.scores


@pytest.mark.usefixtures("patch_central_database")
class TestIntermediateScores:
    async def test_threshold_and_inverter_preserve_each_judgment_async(self, sqlite_instance: MemoryInterface) -> None:
        evidence = ContentScorable(value="evidence")
        children = [
            Score(
                score_type="float_scale",
                score_value=value,
                score_metadata={"source": category},
                score_category=[category],
                scorable=evidence,
            )
            for value, category in (("0.2", "first"), ("0.8", "second"))
        ]
        leaf = _FloatScorer(scores=children)
        for child in children:
            child.scorer_class_identifier = leaf.get_identifier()
        originals = [child.model_copy(deep=True) for child in children]
        threshold = FloatScaleThresholdScorer(scorer=leaf, threshold=0.5)
        inverter = TrueFalseInverterScorer(scorer=threshold)

        with patch.object(
            sqlite_instance, "add_scores_to_memory_async", wraps=sqlite_instance.add_scores_to_memory_async
        ) as add:
            result = (await inverter.score_async(scorable=evidence))[0]

        add.assert_awaited_once()
        assert children == originals
        assert result.get_value() is False
        # The threshold's float describes the uninverted verdict, so only the retained threshold result keeps it.
        assert "original_float_value" not in (result.score_metadata or {})
        entries = sqlite_instance._query_entries(ScoreEntry)
        stored = [entry.get_score() for entry in entries]
        assert len(stored) == 4
        assert len({score.id for score in stored}) == 4
        assert sum(entry.is_intermediate for entry in entries) == 3
        assert next(entry for entry in entries if not entry.is_intermediate).id == result.id
        for original in originals:
            retained = next(score for score in stored if score.id == original.id)
            assert retained.score_value == original.score_value
            assert retained.score_type == original.score_type
            assert retained.score_metadata == original.score_metadata
            assert retained.score_category == original.score_category
            assert retained.timestamp == original.timestamp
            assert retained.scorer_class_identifier.class_name == "_FloatScorer"
            assert "is_intermediate" not in retained.model_dump()
        threshold_result = next(
            score for score in stored if score.scorer_class_identifier.class_name == "FloatScaleThresholdScorer"
        )
        assert threshold_result.get_value() is True
        assert threshold_result.score_metadata["original_float_value"] == 0.8
        assert threshold_result.id != result.id
        assert await sqlite_instance.get_scores_async(score_type="float_scale") == []
        assert len(await sqlite_instance.get_scores_async(score_type="float_scale", include_intermediate=True)) == 2
        assert len(await sqlite_instance.get_scores_async(score_ids=[str(children[0].id)])) == 1

    async def test_message_queries_exclude_intermediate_results_async(self, sqlite_instance: MemoryInterface) -> None:
        piece = MessagePiece(role="assistant", original_value="evidence", conversation_id=str(uuid.uuid4()))
        await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[piece])
        scorer = TrueFalseInverterScorer(scorer=SubStringScorer(substring="evidence"))
        roots = await scorer.score_async(scorable=MessageScorable.from_message(piece.to_message()))

        with patch.object(sqlite_instance, "_query_entries", wraps=sqlite_instance._query_entries) as query:
            stored_roots = await sqlite_instance.get_scores_async(score_type="true_false")
            prompt_scores = await sqlite_instance.get_prompt_scores_async(prompt_ids=[piece.id])
        assert [score.id for score in stored_roots] == [roots[0].id]
        assert [score.id for score in prompt_scores] == [roots[0].id]
        assert len(await sqlite_instance.get_prompt_scores_async(prompt_ids=[piece.id], include_intermediate=True)) == 2

        def check_relationships(session: Session) -> None:
            entry = session.get(PromptMemoryEntry, piece.id)
            assert entry is not None
            assert [score.id for score in entry.scores] == [roots[0].id]
            assert len(entry.all_scores) == 2

        await run_memory_session_async(memory=sqlite_instance, operation=check_relationships)
        assert await sqlite_instance.get_scores_async() == []
        assert await sqlite_instance.get_scores_async(score_ids=[]) == []

        statements = [
            select(ScoreEntry).where(call.kwargs["conditions"])
            for call in query.call_args_list
            if (call.args[0] if call.args else call.kwargs["model_class"]) is ScoreEntry
        ]
        assert len(statements) == 3
        statements.append(select(PromptMemoryEntry).join(PromptMemoryEntry.scores))
        for statement in statements:
            sql = str(statement.compile(dialect=mssql.dialect()))
            assert "is_intermediate = 0" in sql
            assert "IS 0" not in sql
            assert "IS 1" not in sql

    async def test_message_deletion_handles_intermediate_foreign_keys_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        with sqlite_instance.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys = ON")
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1

        piece = MessagePiece(role="assistant", original_value="evidence", conversation_id=str(uuid.uuid4()), sequence=1)
        await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[piece])
        scorer = TrueFalseInverterScorer(scorer=SubStringScorer(substring="evidence"))
        await scorer.score_async(scorable=MessageScorable.from_message(piece.to_message()))

        removed = await sqlite_instance.delete_conversation_pieces_after_sequence_async(
            conversation_id=piece.conversation_id, sequence=0
        )

        assert removed == 1
        entries = sqlite_instance._query_entries(ScoreEntry)
        assert len(entries) == 2
        assert all(entry.prompt_request_response_id is None for entry in entries)
        assert sum(entry.is_intermediate for entry in entries) == 1
        with sqlite_instance.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []

    @pytest.mark.parametrize("role", ["assistant", "user"])
    @pytest.mark.parametrize("supported_role", ["assistant", "user"])
    async def test_audio_retains_transcript_role_without_adding_a_turn_async(
        self,
        *,
        sqlite_instance: MemoryInterface,
        tmp_path: Path,
        role: ChatMessageRole,
        supported_role: ChatMessageRole,
    ) -> None:
        audio_path = tmp_path / "audio.wav"
        audio_path.write_bytes(b"test audio")
        piece = MessagePiece(
            role=role,
            original_value=str(audio_path),
            original_value_data_type="audio_path",
            conversation_id=str(uuid.uuid4()),
        )
        await sqlite_instance.add_message_pieces_to_memory_async(message_pieces=[piece])
        child = SubStringScorer(
            substring="evidence",
            validator=ScorerPromptValidator(supported_data_types=["text"], supported_roles=[supported_role]),
        )
        scorer = AudioTrueFalseScorer(text_capable_scorer=child)
        with patch.object(
            scorer._audio_helper, "_transcribe_audio_async", new_callable=AsyncMock, return_value="evidence"
        ):
            roots = await scorer.score_async(scorable=MessageScorable.from_message(piece.to_message()))

        assert len(await sqlite_instance.get_message_pieces_async(conversation_id=piece.conversation_id)) == 1
        stored = await sqlite_instance.get_scores_async(score_type="true_false", include_intermediate=True)
        if role != supported_role:
            assert roots == []
            assert stored == []
            return
        assert len(roots) == 1
        assert roots[0].get_value() is True
        assert len(stored) == 2
        intermediate = next(score for score in stored if score.id != roots[0].id)
        assert isinstance(intermediate.scorable, ContentEntryScorable)
        content = await sqlite_instance.get_scorable_content_async(content_ids=[intermediate.scorable.content_id])
        assert content[intermediate.scorable.content_id].value == "evidence"

    async def test_batch_roots_do_not_share_intermediate_results_async(self, sqlite_instance: MemoryInterface) -> None:
        scorer = TrueFalseCompositeScorer(
            aggregator=TrueFalseScoreAggregator.OR,
            scorers=[
                TrueFalseInverterScorer(scorer=SubStringScorer(substring="first")),
                SubStringScorer(substring="second"),
            ],
        )
        roots = await scorer.score_batch_async(
            scorables=[ContentScorable(value="first"), ContentScorable(value="second")], batch_size=2
        )

        assert len(roots) == 2
        stored = await sqlite_instance.get_scores_async(score_type="true_false", include_intermediate=True)
        assert len(stored) == 8
        for root in roots:
            matching = [score for score in stored if score.scorable == root.scorable]
            assert len(matching) == 4
            assert sum(score.id in {item.id for item in roots} for score in matching) == 1

    @pytest.mark.parametrize("cancelled", [False, True])
    async def test_failed_root_discards_collection_async(
        self, *, sqlite_instance: MemoryInterface, cancelled: bool
    ) -> None:
        leaf = SubStringScorer(substring="evidence")
        scorer = TrueFalseInverterScorer(scorer=leaf)
        error = asyncio.CancelledError() if cancelled else ValueError("failed wrapper")
        with patch.object(scorer, "_invert", side_effect=error):
            with pytest.raises(asyncio.CancelledError if cancelled else RuntimeError):
                await scorer.score_async(scorable=ContentScorable(value="evidence"))

        assert sqlite_instance._query_entries(ScoreEntry) == []
        roots = await leaf.score_text_async(text="evidence")
        assert len(roots) == 1
        assert len(sqlite_instance._query_entries(ScoreEntry)) == 1
        assert [score.id for score in await sqlite_instance.get_scores_async(score_type="true_false")] == [roots[0].id]

    async def test_root_and_intermediates_roll_back_together_async(self, sqlite_instance: MemoryInterface) -> None:
        scorer = TrueFalseInverterScorer(scorer=SubStringScorer(substring="evidence"))

        def fail_commit(session: Session) -> None:
            session.flush()
            raise ValueError("failed transaction")

        with patch.object(Session, "commit", autospec=True, side_effect=fail_commit):
            with pytest.raises(ValueError, match="failed transaction"):
                await scorer.score_async(scorable=ContentScorable(value="evidence"))

        assert sqlite_instance._query_entries(ScoreEntry) == []
        assert sqlite_instance._query_entries(ScorableContentEntry) == []
