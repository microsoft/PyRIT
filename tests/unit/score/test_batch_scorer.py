# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from collections.abc import MutableSequence
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import MockPromptTarget, get_sample_conversations

from pyrit.executor.attack import PromptSendingAttack
from pyrit.executor.attack.compound import SequentialAttack, SequentialChildAttack
from pyrit.memory import CentralMemory, MemoryInterface, SQLiteMemory
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    AttackResultSelection,
    AttackSeedGroup,
    Message,
    MessagePiece,
    SeedObjective,
)
from pyrit.score import BatchScorer, SubStringScorer


@pytest.fixture
def sample_conversations() -> MutableSequence[Message]:
    return get_sample_conversations()


@pytest.mark.usefixtures("patch_central_database")
class TestBatchScorerInitialization:
    """Test BatchScorer initialization scenarios."""

    def test_init_with_default_batch_size(self) -> None:
        """Test initialization with default batch size."""
        with patch.object(CentralMemory, "get_memory_instance", return_value=MagicMock()):
            batch_scorer = BatchScorer()
            assert batch_scorer._batch_size == 10

    def test_init_with_custom_batch_size(self) -> None:
        """Test initialization with custom batch size."""
        with patch.object(CentralMemory, "get_memory_instance", return_value=MagicMock()):
            batch_scorer = BatchScorer(batch_size=5)
            assert batch_scorer._batch_size == 5

    def test_init_memory_initialization(self) -> None:
        """Test that memory is properly initialized from CentralMemory."""
        mock_memory = MagicMock(spec=MemoryInterface)
        with patch.object(CentralMemory, "get_memory_instance", return_value=mock_memory) as mock_get_memory:
            batch_scorer = BatchScorer()

            mock_get_memory.assert_called_once()
            assert batch_scorer._memory == mock_memory


@pytest.mark.usefixtures("patch_central_database")
class TestBatchScorerScoreResponsesByFilters:
    """Test score_responses_by_filters_async method functionality."""

    async def test_score_responses_by_filters_basic_functionality(
        self, sample_conversations: MutableSequence[Message]
    ) -> None:
        """Test basic scoring functionality with filters."""
        memory = MagicMock(spec=MemoryInterface)
        memory.get_message_pieces_async = AsyncMock(return_value=[sample_conversations[1].message_pieces[0]])

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            scorer = MagicMock()
            test_score = MagicMock()
            scorer.score_batch_async = AsyncMock(return_value=[test_score])

            batch_scorer = BatchScorer()

            scores = await batch_scorer.score_responses_by_filters_async(
                scorer=scorer, conversation_id=str(uuid.uuid4())
            )

            memory.get_message_pieces_async.assert_called_once()
            scorer.score_batch_async.assert_called_once()
            assert scores[0] == test_score

    async def test_score_responses_by_filters_with_all_parameters(
        self, sample_conversations: MutableSequence[Message]
    ) -> None:
        """Test scoring with all filter parameters."""
        memory = MagicMock(spec=MemoryInterface)
        memory.get_message_pieces_async = AsyncMock(return_value=[sample_conversations[1].message_pieces[0]])
        test_conversation_id = str(uuid.uuid4())
        memory.get_attack_results_async = AsyncMock(
            return_value=[
                MagicMock(spec=AttackResult, conversation_id=test_conversation_id),
                MagicMock(spec=AttackResult, conversation_id="other-conversation"),
            ]
        )

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            scorer = MagicMock()
            scorer.score_batch_async = AsyncMock(return_value=[])

            batch_scorer = BatchScorer()

            test_prompt_ids = ["id1", "id2"]
            test_labels = {"test": "value"}
            test_data_type = "text"
            test_not_data_type = "image"

            await batch_scorer.score_responses_by_filters_async(
                scorer=scorer,
                conversation_id=test_conversation_id,
                prompt_ids=test_prompt_ids,
                labels=test_labels,
                data_type=test_data_type,
                not_data_type=test_not_data_type,
            )

            # Labels select attack results first; messages are then filtered without labels.
            memory.get_attack_results_async.assert_awaited_once_with(
                labels=test_labels, result_selection=AttackResultSelection.ALL_RESULTS
            )
            memory.get_message_pieces_async.assert_called_once_with(
                conversation_id=test_conversation_id,
                prompt_ids=test_prompt_ids,
                sent_after=None,
                sent_before=None,
                original_values=None,
                converted_values=None,
                data_type=test_data_type,
                not_data_type=test_not_data_type,
                converted_value_sha256=None,
            )

    async def test_score_responses_by_filters_raises_error_no_matching_filters(self) -> None:
        """Test that ValueError is raised when no entries match filters."""
        memory = MagicMock(spec=MemoryInterface)
        memory.get_message_pieces_async = AsyncMock(return_value=[])

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            batch_scorer = BatchScorer()

            with pytest.raises(ValueError, match="No entries match the provided filters. Please check your filters."):
                await batch_scorer.score_responses_by_filters_async(
                    scorer=MagicMock(),
                    labels={"operation": "nonexistent_op", "operator": "nonexistent_user"},
                )


@pytest.mark.usefixtures("patch_central_database")
class TestBatchScorerUtilityMethods:
    """Test utility methods of BatchScorer."""

    def test_remove_duplicates(self) -> None:
        """Test removal of duplicate message pieces."""
        prompt_id1 = uuid.uuid4()
        prompt_id2 = uuid.uuid4()

        with patch.object(CentralMemory, "get_memory_instance", return_value=MagicMock()):
            batch_scorer = BatchScorer()

            pieces = [
                MessagePiece(
                    id=prompt_id1,
                    role="user",
                    original_value="original prompt text",
                    converted_value="Hello, how are you?",
                    sequence=0,
                ),
                MessagePiece(
                    id=prompt_id2,
                    role="assistant",
                    original_value="original prompt text",
                    converted_value="I'm fine, thank you!",
                    sequence=1,
                ),
                MessagePiece(
                    role="user",
                    original_value="original prompt text",
                    converted_value="Hello, how are you?",
                    sequence=0,
                    original_prompt_id=prompt_id1,
                ),
                MessagePiece(
                    role="assistant",
                    original_value="original prompt text",
                    converted_value="I'm fine, thank you!",
                    sequence=1,
                    original_prompt_id=prompt_id2,
                ),
            ]

            orig_pieces = batch_scorer._remove_duplicates(pieces)

            assert len(orig_pieces) == 2
            for piece in orig_pieces:
                assert piece.id in [prompt_id1, prompt_id2]
                assert piece.id == piece.original_prompt_id


@pytest.mark.usefixtures("patch_central_database")
class TestBatchScorerErrorHandling:
    """Test error handling scenarios for BatchScorer."""

    async def test_score_responses_by_filters_no_filters_provided(
        self, sample_conversations: MutableSequence[Message]
    ) -> None:
        """Test scoring when no filters are provided."""
        memory = MagicMock(spec=MemoryInterface)
        memory.get_message_pieces_async = AsyncMock(return_value=[sample_conversations[1].message_pieces[0]])

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            scorer = MagicMock()
            scorer.score_batch_async = AsyncMock(return_value=[])

            batch_scorer = BatchScorer()

            await batch_scorer.score_responses_by_filters_async(scorer=scorer)

            # Should call memory with all None parameters
            memory.get_message_pieces_async.assert_called_once_with(
                conversation_id=None,
                prompt_ids=None,
                sent_after=None,
                sent_before=None,
                original_values=None,
                converted_values=None,
                data_type=None,
                not_data_type=None,
                converted_value_sha256=None,
            )

    async def test_score_responses_by_filters_handles_multiple_conversations(self) -> None:
        """Test that scoring handles pieces from multiple conversations correctly."""
        memory = MagicMock(spec=MemoryInterface)

        # Create pieces from different conversations
        pieces = [
            MessagePiece(
                role="user",
                conversation_id="conv1",
                sequence=0,
                original_value="Conv1 message",
            ),
            MessagePiece(
                role="assistant",
                conversation_id="conv1",
                sequence=1,
                original_value="Conv1 response",
            ),
            MessagePiece(
                role="user",
                conversation_id="conv2",
                sequence=0,
                original_value="Conv2 message",
            ),
            MessagePiece(
                role="assistant",
                conversation_id="conv2",
                sequence=1,
                original_value="Conv2 response",
            ),
        ]

        memory.get_message_pieces_async = AsyncMock(return_value=pieces)

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            scorer = MagicMock()
            test_scores = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
            scorer.score_batch_async = AsyncMock(return_value=test_scores)

            batch_scorer = BatchScorer()

            scores = await batch_scorer.score_responses_by_filters_async(
                scorer=scorer,
                conversation_id="conv1",
            )

            # Should successfully group by conversation and sequence
            scorer.score_batch_async.assert_called_once()
            call_args = scorer.score_batch_async.call_args
            scorables = call_args.kwargs["scorables"]

            # Should have 4 groups: 2 sequences from conv1, 2 sequences from conv2
            assert len(scorables) == 4
            assert len(scores) == 4

    async def test_score_responses_by_filters_groups_by_sequence_within_conversation(self) -> None:
        """Test that pieces are properly grouped by sequence within each conversation."""
        memory = MagicMock(spec=MemoryInterface)

        # Create multiple pieces in the same sequence
        pieces = [
            MessagePiece(
                role="system",
                conversation_id="conv1",
                sequence=0,
                original_value="System prompt",
            ),
            MessagePiece(
                role="user",
                conversation_id="conv1",
                sequence=1,
                original_value="User message 1",
            ),
            MessagePiece(
                role="user",
                conversation_id="conv1",
                sequence=1,
                original_value="User message 2",
            ),
            MessagePiece(
                role="assistant",
                conversation_id="conv1",
                sequence=2,
                original_value="Assistant response",
            ),
        ]

        memory.get_message_pieces_async = AsyncMock(return_value=pieces)

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            scorer = MagicMock()
            scorer.score_batch_async = AsyncMock(return_value=[MagicMock(), MagicMock()])

            batch_scorer = BatchScorer()

            await batch_scorer.score_responses_by_filters_async(scorer=scorer, conversation_id="conv1")

            # Verify grouping
            call_args = scorer.score_batch_async.call_args
            scorables = call_args.kwargs["scorables"]

            # Should have 3 groups: sequence 0 (with 1 pieces) and sequence 1 (with 2 pieces)
            assert len(scorables) == 3
            assert len(scorables[0].message_piece_ids) == 1
            assert len(scorables[1].message_piece_ids) == 2
            assert len(scorables[2].message_piece_ids) == 1

    async def test_score_responses_by_filters_removes_duplicate_message_pieces(self) -> None:
        """Test that duplicate message pieces are filtered out before batch scoring."""
        memory = MagicMock(spec=MemoryInterface)
        original_piece_id = uuid.uuid4()

        pieces = [
            MessagePiece(
                id=original_piece_id,
                role="assistant",
                conversation_id="conv1",
                sequence=1,
                original_value="Original response",
            ),
            MessagePiece(
                role="assistant",
                conversation_id="conv1",
                sequence=1,
                original_value="Duplicate response copy",
                original_prompt_id=original_piece_id,
            ),
        ]

        memory.get_message_pieces_async = AsyncMock(return_value=pieces)

        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            scorer = MagicMock()
            scorer.score_batch_async = AsyncMock(return_value=[])

            batch_scorer = BatchScorer()

            await batch_scorer.score_responses_by_filters_async(scorer=scorer, conversation_id="conv1")

            call_args = scorer.score_batch_async.call_args
            scorables = call_args.kwargs["scorables"]

            assert len(scorables) == 1
            assert len(scorables[0].message_piece_ids) == 1
            assert scorables[0].message_piece_ids[0] == original_piece_id


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("narrow_to", "expected"),
    [(None, {"conv-a", "conv-c"}), ("conv-a", {"conv-a"}), ("conv-b", set())],
)
async def test_score_responses_by_labels_uses_the_labelled_attack_conversations_async(
    *, sqlite_instance: SQLiteMemory, narrow_to: str | None, expected: set[str]
) -> None:
    for conversation_id, operation in (("conv-a", "op-a"), ("conv-b", "op-b"), ("conv-c", "op-a")):
        await sqlite_instance.add_message_pieces_to_memory_async(
            message_pieces=[
                MessagePiece(role="user", original_value="ask", conversation_id=conversation_id, sequence=0),
                MessagePiece(role="assistant", original_value="reply", conversation_id=conversation_id, sequence=1),
            ]
        )
        await sqlite_instance.add_attack_results_to_memory_async(
            attack_results=[
                AttackResult(
                    conversation_id=conversation_id,
                    objective="objective",
                    outcome=AttackOutcome.FAILURE,
                    labels={"operation": operation},
                )
            ]
        )
    batch_scorer = BatchScorer()
    scorer = SubStringScorer(substring="reply", categories=["test"])

    if not expected:
        # conv-b isn't labelled op-a, so narrowing to it leaves nothing to score.
        with pytest.raises(ValueError, match="No entries match"):
            await batch_scorer.score_responses_by_filters_async(
                scorer=scorer, labels={"operation": "op-a"}, conversation_id=narrow_to
            )
        return
    scores = await batch_scorer.score_responses_by_filters_async(
        scorer=scorer, labels={"operation": "op-a"}, conversation_id=narrow_to
    )

    scored = await sqlite_instance.get_message_pieces_async(prompt_ids=[score.message_piece_id for score in scores])
    assert {piece.conversation_id for piece in scored} == expected
    replies = {piece.id for piece in scored if piece.api_role == "assistant"}
    assert all(score.get_value() for score in scores if score.message_piece_id in replies)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("child_overrides_label", [False, True])
async def test_score_responses_by_labels_skips_results_without_a_conversation_async(
    *, sqlite_instance: SQLiteMemory, child_overrides_label: bool
) -> None:
    """SequentialAttack saves an envelope with an empty conversation_id, which must not widen the lookup."""
    target = MockPromptTarget()
    child = SequentialChildAttack(
        strategy=PromptSendingAttack(objective_target=target),
        seed_group=AttackSeedGroup(seeds=[SeedObjective(value="labelled objective")]),
        memory_labels={"operation": "child-only"} if child_overrides_label else {},
    )
    result = await SequentialAttack(objective_target=target, child_attacks=[child]).execute_async(
        objective="labelled objective", memory_labels={"operation": "op-seq"}
    )
    await PromptSendingAttack(objective_target=target).execute_async(objective="unrelated objective")
    batch_scorer = BatchScorer()
    scorer = SubStringScorer(substring="never present", categories=["test"])

    if child_overrides_label:
        # Only the envelope carries op-seq, and it has no conversation of its own.
        with pytest.raises(ValueError, match="No entries match"):
            await batch_scorer.score_responses_by_filters_async(scorer=scorer, labels={"operation": "op-seq"})
        return
    scores = await batch_scorer.score_responses_by_filters_async(scorer=scorer, labels={"operation": "op-seq"})

    scored = await sqlite_instance.get_message_pieces_async(prompt_ids=[score.message_piece_id for score in scores])
    child_ids = set(result.metadata[SequentialAttack.CHILD_ATTACK_RESULT_IDS_KEY])
    [child_result] = [
        row
        for row in await sqlite_instance.get_attack_results_async(result_selection=AttackResultSelection.ALL_RESULTS)
        if row.attack_result_id in child_ids
    ]
    assert {piece.conversation_id for piece in scored} == {child_result.conversation_id}
    assert len(scores) == len({score.message_piece_id for score in scores})


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("key", ["team name", "caf\u00e9", 'team"name', "team[0]"])
async def test_score_responses_by_labels_accepts_literal_label_keys_async(
    *, sqlite_instance: SQLiteMemory, key: str
) -> None:
    for conversation_id, value in (("conv-a", "review"), ("conv-b", "other")):
        await sqlite_instance.add_message_pieces_to_memory_async(
            message_pieces=[
                MessagePiece(role="user", original_value="ask", conversation_id=conversation_id, sequence=0),
                MessagePiece(role="assistant", original_value="reply", conversation_id=conversation_id, sequence=1),
            ]
        )
        await sqlite_instance.add_attack_results_to_memory_async(
            attack_results=[
                AttackResult(
                    conversation_id=conversation_id,
                    objective="objective",
                    outcome=AttackOutcome.FAILURE,
                    labels={key: value},
                )
            ]
        )

    scores = await BatchScorer().score_responses_by_filters_async(
        scorer=SubStringScorer(substring="reply", categories=["test"]), labels={key: "review"}
    )

    scored = await sqlite_instance.get_message_pieces_async(prompt_ids=[score.message_piece_id for score in scores])
    assert {piece.conversation_id for piece in scored} == {"conv-a"}


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("conversation_id", "expected"), [(None, {"attached", "independent"}), ("independent", {"independent"})]
)
async def test_score_responses_with_empty_labels_is_unfiltered_async(
    *, sqlite_instance: SQLiteMemory, conversation_id: str | None, expected: set[str]
) -> None:
    for owner in ("attached", "independent"):
        await sqlite_instance.add_message_pieces_to_memory_async(
            message_pieces=[MessagePiece(role="assistant", original_value="reply", conversation_id=owner)]
        )
    await sqlite_instance.add_attack_results_to_memory_async(
        attack_results=[
            AttackResult(
                conversation_id="attached", objective="objective", outcome=AttackOutcome.FAILURE, labels={"op": "a"}
            )
        ]
    )

    scores = await BatchScorer().score_responses_by_filters_async(
        scorer=SubStringScorer(substring="reply", categories=["test"]), labels={}, conversation_id=conversation_id
    )

    scored = await sqlite_instance.get_message_pieces_async(prompt_ids=[score.message_piece_id for score in scores])
    assert {piece.conversation_id for piece in scored} == expected
