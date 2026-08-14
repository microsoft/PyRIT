# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses
import uuid

import pytest

from pyrit.memory import MemoryInterface
from pyrit.models import ComponentIdentifier, Message, MessagePiece, Score, ScoringExpectation
from pyrit.score import (
    ContentScorable,
    MessageReferenceScorable,
    MessageScorable,
    MessageScorer,
    Scorable,
    Scorer,
    ScorerPromptValidator,
    TrueFalseScorer,
)
from pyrit.score.message_scorer import extract_objective_from_previous_turn


@dataclasses.dataclass(frozen=True)
class UnsupportedScorable(Scorable):
    """A scorable kind no message scorer handles."""

    uri: str


class PermissiveValidator(ScorerPromptValidator):
    def validate(self, message, objective=None):
        pass

    def is_message_piece_supported(self, message_piece):
        return True


class RecordingScorer(TrueFalseScorer):
    """A message scorer that remembers what it was asked to score."""

    def __init__(self):
        super().__init__(validator=PermissiveValidator())
        self.scored_messages: list[Message] = []
        self.scored_objectives: list[str | None] = []

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
        self.scored_messages.append(message)
        self.scored_objectives.append(objective)
        return [self._build_score(message.get_piece(), objective)]

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        return [self._build_score(message_piece, objective)]

    def _build_score(self, message_piece: MessagePiece, objective: str | None) -> Score:
        return Score(
            score_value="true",
            score_value_description="desc",
            score_type="true_false",
            score_category=None,
            score_metadata=None,
            score_rationale="rationale",
            scorer_class_identifier=self.get_identifier(),
            message_piece_id=message_piece.id,
            objective=objective,
        )


def _assistant_message(value: str = "response", conversation_id: str | None = None) -> Message:
    return MessagePiece(
        role="assistant",
        original_value=value,
        conversation_id=conversation_id or str(uuid.uuid4()),
    ).to_message()


@pytest.mark.usefixtures("patch_central_database")
class TestScorableResolution:
    """MessageScorer reduces every message-shaped scorable to a single Message."""

    async def test_message_scorable_is_scored_directly(self):
        scorer = RecordingScorer()
        message = _assistant_message()

        scores = await scorer.score_async(scorable=MessageScorable(message=message))

        assert len(scores) == 1
        assert scorer.scored_messages == [message]

    async def test_message_reference_scorable_resolves_from_memory(self, sqlite_instance: MemoryInterface):
        message = _assistant_message("stored response")
        sqlite_instance.add_message_to_memory(request=message)
        piece_id = message.get_piece().id
        scorer = RecordingScorer()

        scores = await scorer.score_async(scorable=MessageReferenceScorable(message_piece_ids=(piece_id,)))

        assert len(scores) == 1
        assert scorer.scored_messages[0].get_value() == "stored response"

    async def test_message_reference_scorable_not_in_memory_raises(self):
        scorer = RecordingScorer()
        missing_id = uuid.uuid4()

        with pytest.raises(ValueError, match="No message pieces found in memory"):
            await scorer.score_async(scorable=MessageReferenceScorable(message_piece_ids=(missing_id,)))

    async def test_message_reference_scorable_partially_in_memory_raises(self, sqlite_instance: MemoryInterface):
        """A partial resolution is a caller error, so it must not be scored silently."""
        stored = _assistant_message("stored response")
        sqlite_instance.add_message_to_memory(request=stored)
        stored_id = stored.get_piece().id
        missing_id = uuid.uuid4()
        scorer = RecordingScorer()

        with pytest.raises(ValueError, match=f"No message pieces found in memory for ids \\['{missing_id}'\\]"):
            await scorer.score_async(scorable=MessageReferenceScorable(message_piece_ids=(stored_id, missing_id)))

        assert scorer.scored_messages == []

    async def test_message_reference_scorable_spanning_messages_raises(self, sqlite_instance: MemoryInterface):
        conversation_id = str(uuid.uuid4())
        first = MessagePiece(
            role="user", original_value="ask", conversation_id=conversation_id, sequence=0
        ).to_message()
        second = MessagePiece(
            role="assistant", original_value="answer", conversation_id=conversation_id, sequence=1
        ).to_message()
        sqlite_instance.add_message_to_memory(request=first)
        sqlite_instance.add_message_to_memory(request=second)
        scorer = RecordingScorer()

        with pytest.raises(ValueError, match="exactly one message"):
            await scorer.score_async(
                scorable=MessageReferenceScorable(
                    message_piece_ids=(first.get_piece().id, second.get_piece().id),
                )
            )

    async def test_content_scorable_is_never_persisted(self):
        scorer = RecordingScorer()

        scores = await scorer.score_async(scorable=ContentScorable(value="loose text"))

        scored_piece = scorer.scored_messages[0].get_piece()
        assert scored_piece.original_value == "loose text"
        assert scored_piece.role == "user"
        assert scored_piece.not_in_memory is True
        # Memory cannot link a score to a piece it never stored.
        assert scores[0].message_piece_id is None

    async def test_unsupported_scorable_raises_type_error(self):
        scorer = RecordingScorer()

        with pytest.raises(TypeError, match="cannot score UnsupportedScorable"):
            await scorer.score_async(scorable=UnsupportedScorable(uri="/tmp/out.txt"))  # type: ignore[arg-type]


class TestScorerBaseIsScorableAgnostic:
    """Scorer knows nothing about messages; the message hooks belong to MessageScorer."""

    def test_scorer_requires_a_scorable_implementation(self):
        # A scorer that implements only the message hooks cannot be instantiated. Without
        # this, such a scorer builds fine and fails later with a confusing TypeError.
        assert "_score_scorable_async" in Scorer.__abstractmethods__

    @pytest.mark.parametrize("hook", ["_score_async", "_score_piece_async", "_get_supported_pieces"])
    def test_message_hooks_live_on_message_scorer(self, hook):
        assert not hasattr(Scorer, hook)
        assert hasattr(MessageScorer, hook)

    def test_message_scorer_satisfies_the_scorable_contract(self):
        assert "_score_scorable_async" not in MessageScorer.__abstractmethods__
        assert "_score_piece_async" in MessageScorer.__abstractmethods__


@pytest.mark.usefixtures("patch_central_database")
class TestScorableFilters:
    """role_filter and skip_on_error_result are fields on the scorable, not call parameters."""

    async def test_role_filter_mismatch_skips_scoring(self):
        scorer = RecordingScorer()
        message = _assistant_message()

        scores = await scorer.score_async(scorable=MessageScorable(message=message, role_filter="user"))

        assert scores == []
        assert scorer.scored_messages == []

    async def test_role_filter_match_scores(self):
        scorer = RecordingScorer()
        message = _assistant_message()

        scores = await scorer.score_async(scorable=MessageScorable(message=message, role_filter="assistant"))

        assert len(scores) == 1

    async def test_skip_on_error_result_skips_error_message(self):
        scorer = RecordingScorer()
        message = MessagePiece(
            role="assistant",
            original_value="blocked",
            original_value_data_type="error",
            response_error="blocked",
        ).to_message()

        scores = await scorer.score_async(scorable=MessageScorable(message=message, skip_on_error_result=True))

        assert scores == []
        assert scorer.scored_messages == []

    async def test_error_message_is_scored_when_not_skipping(self):
        scorer = RecordingScorer()
        message = MessagePiece(
            role="assistant",
            original_value="blocked",
            original_value_data_type="error",
            response_error="blocked",
        ).to_message()

        scores = await scorer.score_async(scorable=MessageScorable(message=message))

        assert len(scores) == 1


@pytest.mark.usefixtures("patch_central_database")
class TestExpectation:
    """The expectation carries what to look for."""

    async def test_objective_reaches_the_scorer(self):
        scorer = RecordingScorer()

        await scorer.score_async(
            scorable=MessageScorable(message=_assistant_message()),
            expectation=ScoringExpectation(objective="find the objective"),
        )

        assert scorer.scored_objectives == ["find the objective"]

    async def test_no_expectation_means_no_objective(self):
        scorer = RecordingScorer()

        await scorer.score_async(scorable=MessageScorable(message=_assistant_message()))

        assert scorer.scored_objectives == [None]


@pytest.mark.usefixtures("patch_central_database")
class TestDeprecatedParameters:
    """The legacy message-shaped parameters survive one release behind a warning."""

    async def test_positional_message_maps_to_message_scorable(self):
        scorer = RecordingScorer()
        message = _assistant_message()

        with pytest.warns(DeprecationWarning, match="Scorer.score_async"):
            scores = await scorer.score_async(message)

        assert len(scores) == 1
        assert scorer.scored_messages == [message]

    async def test_keyword_message_maps_to_message_scorable(self):
        scorer = RecordingScorer()
        message = _assistant_message()

        with pytest.warns(DeprecationWarning, match="Scorer.score_async"):
            await scorer.score_async(message=message)

        assert scorer.scored_messages == [message]

    async def test_message_does_not_widen_to_the_stored_conversation(self, sqlite_instance: MemoryInterface):
        """The shim scores the supplied message, never the whole conversation behind it."""
        conversation_id = str(uuid.uuid4())
        sqlite_instance.add_message_to_memory(
            request=MessagePiece(
                role="user",
                original_value="an earlier turn that must not be scored",
                conversation_id=conversation_id,
                sequence=0,
            ).to_message()
        )
        message = _assistant_message("only this turn", conversation_id=conversation_id)
        sqlite_instance.add_message_to_memory(request=message)
        scorer = RecordingScorer()

        with pytest.warns(DeprecationWarning, match="Scorer.score_async"):
            await scorer.score_async(message)

        assert [scored.get_value() for scored in scorer.scored_messages] == ["only this turn"]

    async def test_objective_maps_to_expectation(self):
        scorer = RecordingScorer()

        with pytest.warns(DeprecationWarning, match="Scorer.score_async"):
            await scorer.score_async(_assistant_message(), objective="legacy objective")

        assert scorer.scored_objectives == ["legacy objective"]

    async def test_legacy_role_filter_maps_onto_the_scorable(self):
        scorer = RecordingScorer()

        with pytest.warns(DeprecationWarning, match="Scorer.score_async"):
            scores = await scorer.score_async(_assistant_message(), role_filter="user")

        assert scores == []

    async def test_legacy_skip_on_error_result_maps_onto_the_scorable(self):
        scorer = RecordingScorer()
        message = MessagePiece(
            role="assistant",
            original_value="blocked",
            original_value_data_type="error",
            response_error="blocked",
        ).to_message()

        with pytest.warns(DeprecationWarning, match="Scorer.score_async"):
            scores = await scorer.score_async(message, skip_on_error_result=True)

        assert scores == []

    async def test_infer_objective_from_request_reads_the_previous_turn(self, sqlite_instance: MemoryInterface):
        conversation_id = str(uuid.uuid4())
        sqlite_instance.add_message_to_memory(
            request=MessagePiece(
                role="user",
                original_value="the inferred objective",
                conversation_id=conversation_id,
                sequence=0,
            ).to_message()
        )
        message = _assistant_message("response", conversation_id=conversation_id)
        sqlite_instance.add_message_to_memory(request=message)
        scorer = RecordingScorer()

        with pytest.warns(DeprecationWarning, match="Scorer.score_async"):
            await scorer.score_async(message, infer_objective_from_request=True)

        assert scorer.scored_objectives == ["the inferred objective"]

    async def test_new_signature_emits_no_warning(self, recwarn):
        scorer = RecordingScorer()

        await scorer.score_async(
            scorable=MessageScorable(message=_assistant_message()),
            expectation=ScoringExpectation(objective="objective"),
        )

        assert [warning for warning in recwarn if issubclass(warning.category, DeprecationWarning)] == []


@pytest.mark.usefixtures("patch_central_database")
class TestConflictingInputs:
    """The shim refuses input it cannot map without guessing."""

    async def test_message_and_scorable_together_raises(self):
        scorer = RecordingScorer()
        message = _assistant_message()

        with pytest.raises(ValueError, match="not both"):
            await scorer.score_async(message, scorable=MessageScorable(message=message))

    async def test_neither_message_nor_scorable_raises(self):
        scorer = RecordingScorer()

        with pytest.raises(ValueError, match="must be provided"):
            await scorer.score_async()

    async def test_objective_and_expectation_together_raises(self):
        scorer = RecordingScorer()

        with pytest.raises(ValueError, match="not both"):
            await scorer.score_async(
                scorable=MessageScorable(message=_assistant_message()),
                objective="one",
                expectation=ScoringExpectation(objective="two"),
            )

    @pytest.mark.parametrize("kwargs", [{"role_filter": "assistant"}, {"skip_on_error_result": True}])
    async def test_message_flags_with_a_scorable_raises(self, kwargs):
        scorer = RecordingScorer()

        with pytest.raises(ValueError, match="fields on the message scorable"):
            await scorer.score_async(scorable=MessageScorable(message=_assistant_message()), **kwargs)


@pytest.mark.usefixtures("patch_central_database")
class TestExtractObjectiveFromPreviousTurn:
    """The objective lookup belongs to whoever builds the expectation."""

    def test_reads_the_turn_before_the_response(self, sqlite_instance: MemoryInterface):
        conversation_id = str(uuid.uuid4())
        sqlite_instance.add_message_to_memory(
            request=MessagePiece(
                role="user", original_value="the request", conversation_id=conversation_id, sequence=0
            ).to_message()
        )
        message = _assistant_message("the response", conversation_id=conversation_id)
        sqlite_instance.add_message_to_memory(request=message)

        objective = extract_objective_from_previous_turn(message=message, memory=sqlite_instance)

        assert objective == "the request"

    def test_returns_empty_for_a_user_message(self, sqlite_instance: MemoryInterface):
        message = MessagePiece(role="user", original_value="a request").to_message()

        assert extract_objective_from_previous_turn(message=message, memory=sqlite_instance) == ""

    def test_returns_empty_when_the_conversation_is_not_stored(self, sqlite_instance: MemoryInterface):
        message = _assistant_message()

        assert extract_objective_from_previous_turn(message=message, memory=sqlite_instance) == ""
