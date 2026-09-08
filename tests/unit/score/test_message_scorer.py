# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses
import inspect
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.memory.memory_models import ScorableContentEntry
from pyrit.models import (
    ChatMessageRole,
    ComponentIdentifier,
    Condition,
    ContentEntryScorable,
    MatchesObjective,
    Message,
    MessagePiece,
    Score,
    ScoreStatus,
    ScoringExpectation,
)
from pyrit.score import (
    ContentScorable,
    MessageScorable,
    MessageScorer,
    MessageTrueFalseScorer,
    Scorable,
    Scorer,
    ScorerPromptValidator,
)
from pyrit.score.message_scorable_resolver import MessageScorableResolver
from pyrit.score.message_scorer import extract_objective_from_previous_turn


class UnsupportedScorable(Scorable):
    """A scorable kind no message scorer handles."""

    uri: str


class PermissiveValidator(ScorerPromptValidator):
    def __init__(self, *, is_objective_required: bool = False, supported_roles=None) -> None:
        super().__init__(is_objective_required=is_objective_required, supported_roles=supported_roles)

    def validate(self, message, objective=None):
        pass

    def is_message_piece_supported(self, message_piece):
        return True


class RecordingScorer(MessageTrueFalseScorer):
    """A message scorer that remembers what it was asked to score."""

    def __init__(
        self,
        *,
        message_resolver: MessageScorableResolver | None = None,
        is_objective_required: bool = False,
        supported_roles=None,
    ) -> None:
        super().__init__(
            validator=PermissiveValidator(
                is_objective_required=is_objective_required,
                supported_roles=supported_roles,
            ),
            message_resolver=message_resolver,
        )
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
    """Return an assistant message that is already in memory, since a scorable names ids."""
    message = MessagePiece(
        role="assistant",
        original_value=value,
        conversation_id=conversation_id or str(uuid.uuid4()),
    ).to_message()
    CentralMemory.get_memory_instance().add_message_to_memory(request=message)
    return message


def _error_message() -> Message:
    """Return a stored assistant message that carries a blocked error result."""
    message = MessagePiece(
        role="assistant",
        original_value="blocked",
        original_value_data_type="error",
        response_error="blocked",
        conversation_id=str(uuid.uuid4()),
    ).to_message()
    CentralMemory.get_memory_instance().add_message_to_memory(request=message)
    return message


@pytest.mark.usefixtures("patch_central_database")
class TestScorableResolution:
    """MessageScorer reduces every message-shaped scorable to a single Message."""

    async def test_message_scorable_resolves_from_memory(self, sqlite_instance: MemoryInterface):
        message = _assistant_message("stored response")
        scorer = RecordingScorer()

        scores = await scorer.score_async(scorable=MessageScorable.from_message(message))

        assert len(scores) == 1
        assert scorer.scored_messages[0].get_value() == "stored response"

    async def test_message_scorable_resolves_by_piece_id(self, sqlite_instance: MemoryInterface):
        message = _assistant_message("stored response")
        piece_id = message.get_piece().id
        scorer = RecordingScorer()

        scores = await scorer.score_async(scorable=MessageScorable(message_piece_ids=(piece_id,)))

        assert len(scores) == 1
        assert scorer.scored_messages[0].get_value() == "stored response"

    async def test_message_scorable_not_in_memory_raises(self):
        scorer = RecordingScorer()
        missing_id = uuid.uuid4()

        with pytest.raises(ValueError, match="No message pieces found in memory"):
            await scorer.score_async(scorable=MessageScorable(message_piece_ids=(missing_id,)))

    async def test_message_scorable_partially_in_memory_raises(self, sqlite_instance: MemoryInterface):
        """A partial resolution is a caller error, so it must not be scored silently."""
        stored = _assistant_message("stored response")
        stored_id = stored.get_piece().id
        missing_id = uuid.uuid4()
        scorer = RecordingScorer()

        with pytest.raises(ValueError, match=f"No message pieces found in memory for ids \\['{missing_id}'\\]"):
            await scorer.score_async(scorable=MessageScorable(message_piece_ids=(stored_id, missing_id)))

        assert scorer.scored_messages == []

    async def test_message_scorable_spanning_messages_raises(self, sqlite_instance: MemoryInterface):
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
                scorable=MessageScorable(
                    message_piece_ids=(first.get_piece().id, second.get_piece().id),
                )
            )

    async def test_content_scorable_does_not_persist_a_message_piece(self):
        scorer = RecordingScorer()

        scores = await scorer.score_async(scorable=ContentScorable(value="loose text"))

        scored_piece = scorer.scored_messages[0].get_piece()
        assert scored_piece.original_value == "loose text"
        assert scored_piece.role == "user"
        assert scored_piece.not_in_memory is True
        # Memory cannot link a score to a piece it never stored.
        assert scores[0].message_piece_id is None

    async def test_score_image_copies_source_before_persisting_anchor(
        self,
        sqlite_instance: MemoryInterface,
        tmp_path: Path,
    ):
        source = tmp_path / "source.png"
        image_bytes = b"\x89PNG scorer evidence"
        source.write_bytes(image_bytes)
        scorer = RecordingScorer()

        score = (await scorer.score_image_async(str(source)))[0]

        assert isinstance(score.scorable, ContentEntryScorable)
        stored_content = sqlite_instance.get_scorable_content(content_ids=[score.scorable.content_id])[
            score.scorable.content_id
        ]
        assert stored_content.value != str(source)
        source.unlink()
        assert Path(stored_content.value).read_bytes() == image_bytes

        rescored = await scorer.score_async(scorable=score.scorable)
        assert rescored[0].scorable == score.scorable

    async def test_rescoring_stored_content_keeps_its_anchor(self, sqlite_instance: MemoryInterface):
        """Re-scoring must point at the row the content already has, not copy it into a new one."""
        scorer = RecordingScorer()
        first = (await scorer.score_async(scorable=ContentScorable(value="loose text")))[0]
        anchor = first.scorable
        assert isinstance(anchor, ContentEntryScorable)

        second = (await scorer.score_async(scorable=anchor))[0]

        assert second.scorable == anchor
        assert len(sqlite_instance._query_entries(ScorableContentEntry)) == 1

    async def test_message_scorer_uses_injected_resolver(self):
        message = _assistant_message()
        resolver = MagicMock(spec=MessageScorableResolver)
        resolver.resolve.return_value = message
        scorer = RecordingScorer(message_resolver=resolver)

        await scorer.score_async(scorable=ContentScorable(value="ignored"))

        resolver.resolve.assert_called_once()

    async def test_unsupported_scorable_raises_type_error(self):
        scorer = RecordingScorer()

        with pytest.raises(TypeError, match="cannot score UnsupportedScorable"):
            await scorer.score_async(scorable=UnsupportedScorable(uri="/tmp/out.txt"))  # type: ignore[arg-type]

    def test_stamping_an_unknown_anchor_raises_type_error(self):
        score = MagicMock(spec=Score)
        score.scorable = None

        with pytest.raises(TypeError, match="cannot anchor a score"):
            MessageScorer._stamp_scorable(
                message=_assistant_message(),
                scores=[score],
                anchor=UnsupportedScorable(uri="/tmp/out.txt"),
                persisted_piece_ids=None,
            )


class TestScorerBaseIsScorableAgnostic:
    """The base extension contract contains no message-processing requirements."""

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

    def test_message_dependencies_live_on_message_scorer(self):
        # The base keeps 'validator' only as a deprecated shim for pre-2.0 subclasses; the
        # dependency itself is required by MessageScorer.
        assert inspect.signature(Scorer).parameters["validator"].default is None
        assert inspect.signature(MessageScorer).parameters["validator"].default is inspect.Parameter.empty
        for hook in [
            "_build_fallback_score",
            "_apply_structured_refusal_substitution",
            "_apply_blocked_content_substitution",
        ]:
            assert not hasattr(Scorer, hook)
            assert hasattr(MessageScorer, hook)


@pytest.mark.usefixtures("patch_central_database")
class TestScorableFilters:
    """Role policy is a scorer capability, applied after the scorer acquires its evidence."""

    async def test_unread_role_produces_no_score(self):
        scorer = RecordingScorer(supported_roles=["user"])
        message = _assistant_message()

        scores = await scorer.score_async(scorable=MessageScorable.from_message(message))

        assert scores == []
        assert scorer.scored_messages == []

    async def test_read_role_scores(self):
        scorer = RecordingScorer(supported_roles=["assistant"])
        message = _assistant_message()

        scores = await scorer.score_async(scorable=MessageScorable.from_message(message))

        assert len(scores) == 1

    async def test_simulated_assistant_is_unread_by_default(self):
        """A prepended turn is fabricated history, so a scorer must opt in to read it."""
        scorer = RecordingScorer()
        message = MessagePiece(
            role="simulated_assistant",
            original_value="prepended text",
            conversation_id=str(uuid.uuid4()),
        ).to_message()
        CentralMemory.get_memory_instance().add_message_to_memory(request=message)

        scores = await scorer.score_async(scorable=MessageScorable.from_message(message))

        assert scores == []
        assert scorer.scored_messages == []

    async def test_transport_error_message_produces_undetermined_score(self):
        """An error is not the target's answer, so no verdict was reachable."""
        scorer = RecordingScorer()
        message = MessagePiece(
            role="assistant",
            original_value="connection reset",
            original_value_data_type="error",
            response_error="processing",
            conversation_id=str(uuid.uuid4()),
        ).to_message()
        CentralMemory.get_memory_instance().add_message_to_memory(request=message)

        scores = await scorer.score_async(scorable=MessageScorable.from_message(message))

        assert len(scores) == 1
        assert scores[0].status == ScoreStatus.UNDETERMINED
        assert scorer.scored_messages == []

    async def test_blocked_message_stays_false_safe(self):
        """A fully blocked response reaches the scorer's family and keeps its neutral verdict."""
        scorer = RecordingScorer()
        message = _error_message()

        scores = await scorer.score_async(scorable=MessageScorable.from_message(message))

        assert len(scores) == 1
        assert scores[0].score_value == "false"
        assert scorer.scored_messages == []

    async def test_partly_errored_message_scores_only_readable_pieces(self):
        """One bad piece must neither discard nor reach the scorer beside readable content."""
        scorer = RecordingScorer()
        conversation_id = str(uuid.uuid4())
        message = Message(
            message_pieces=[
                MessagePiece(
                    role="assistant",
                    original_value="blocked",
                    original_value_data_type="error",
                    response_error="blocked",
                    conversation_id=conversation_id,
                ),
                MessagePiece(role="assistant", original_value="usable text", conversation_id=conversation_id),
            ]
        )
        CentralMemory.get_memory_instance().add_message_to_memory(request=message)

        scores = await scorer.score_async(scorable=MessageScorable.from_message(message))

        assert len(scores) == 1
        assert len(scorer.scored_messages) == 1
        assert [piece.original_value for piece in scorer.scored_messages[0].message_pieces] == ["usable text"]

    async def test_explicit_legacy_role_filter_still_applies(self):
        scorer = RecordingScorer()
        message = _assistant_message()

        with pytest.warns(DeprecationWarning, match="deprecated"):
            scores = await scorer.score_async(
                scorable=MessageScorable.from_message(message),
                role_filter="user",
            )

        assert scores == []

    async def test_explicit_legacy_skip_on_error_still_applies(self):
        scorer = RecordingScorer()

        with pytest.warns(DeprecationWarning, match="deprecated"):
            scores = await scorer.score_async(
                scorable=MessageScorable.from_message(_error_message()),
                skip_on_error_result=True,
            )

        assert scores == []


@pytest.mark.usefixtures("patch_central_database")
class TestExpectation:
    """The expectation carries what to look for."""

    async def test_objective_reaches_the_scorer(self):
        scorer = RecordingScorer()

        await scorer.score_async(
            scorable=MessageScorable.from_message(_assistant_message()),
            expectation=ScoringExpectation(objective="find the objective"),
        )

        assert scorer.scored_objectives == ["find the objective"]

    async def test_no_expectation_means_no_objective(self):
        scorer = RecordingScorer()

        await scorer.score_async(scorable=MessageScorable.from_message(_assistant_message()))

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

    async def test_ephemeral_message_keeps_its_own_state(self):
        """An in-hand message is scored as it stands, so nothing about it is re-derived."""
        scorer = RecordingScorer()
        message = MessagePiece(
            role="assistant",
            original_value="original",
            converted_value="converted",
        ).to_message()
        message.set_response_not_in_memory()

        with pytest.warns(DeprecationWarning, match="Scorer.score_async"):
            await scorer.score_async(message)

        scored = scorer.scored_messages[0]
        assert scored.get_value() == "converted"
        assert scored.get_piece().role == "assistant"
        assert not scored.is_error()

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
        scorer = RecordingScorer()

        with pytest.warns(DeprecationWarning, match="Scorer.score_async"):
            await scorer.score_async(message)

        assert [scored.get_value() for scored in scorer.scored_messages] == ["only this turn"]

    async def test_objective_maps_to_expectation(self):
        scorer = RecordingScorer()

        with pytest.warns(DeprecationWarning, match="Scorer.score_async"):
            await scorer.score_async(_assistant_message(), objective="legacy objective")

        assert scorer.scored_objectives == ["legacy objective"]

    async def test_retired_role_filter_is_preserved(self):
        scorer = RecordingScorer()

        with pytest.warns(DeprecationWarning, match="deprecated"):
            scores = await scorer.score_async(
                scorable=MessageScorable.from_message(_assistant_message()),
                role_filter="user",
            )

        assert scores == []

    async def test_retired_skip_on_error_result_is_preserved(self):
        scorer = RecordingScorer()

        with pytest.warns(DeprecationWarning, match="deprecated"):
            scores = await scorer.score_async(
                scorable=MessageScorable.from_message(_error_message()),
                skip_on_error_result=True,
            )

        assert scores == []

    async def test_explicit_false_legacy_boolean_emits_warning(self):
        scorer = RecordingScorer()

        with pytest.warns(DeprecationWarning, match="Scorer.score_async"):
            await scorer.score_async(
                scorable=MessageScorable.from_message(_assistant_message()),
                infer_objective_from_request=False,
            )

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
        scorer = RecordingScorer()

        with pytest.warns(DeprecationWarning, match="Scorer.score_async"):
            await scorer.score_async(message, infer_objective_from_request=True)

        assert scorer.scored_objectives == ["the inferred objective"]

    async def test_new_signature_emits_no_warning(self, recwarn):
        scorer = RecordingScorer()

        await scorer.score_async(
            scorable=MessageScorable.from_message(_assistant_message()),
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
            await scorer.score_async(message, scorable=MessageScorable.from_message(message))

    async def test_neither_message_nor_scorable_raises(self):
        scorer = RecordingScorer()

        with pytest.raises(ValueError, match="must be provided"):
            await scorer.score_async()

    async def test_objective_and_expectation_together_raises(self):
        scorer = RecordingScorer()

        with pytest.raises(ValueError, match="not both"):
            await scorer.score_async(
                scorable=MessageScorable.from_message(_assistant_message()),
                objective="one",
                expectation=ScoringExpectation(objective="two"),
            )


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

        objective = extract_objective_from_previous_turn(message=message, memory=sqlite_instance)

        assert objective == "the request"

    def test_returns_empty_for_a_user_message(self, sqlite_instance: MemoryInterface):
        message = MessagePiece(role="user", original_value="a request").to_message()

        assert extract_objective_from_previous_turn(message=message, memory=sqlite_instance) == ""

    def test_returns_empty_when_the_conversation_is_not_stored(self, sqlite_instance: MemoryInterface):
        message = MessagePiece(
            role="assistant", original_value="a response", conversation_id=str(uuid.uuid4())
        ).to_message()

        assert extract_objective_from_previous_turn(message=message, memory=sqlite_instance) == ""

    def test_reads_the_request_for_the_scored_turn_not_the_latest_one(self, sqlite_instance: MemoryInterface):
        """Scoring an earlier response must not pick up a request from later in the conversation."""
        conversation_id = str(uuid.uuid4())
        turns: list[tuple[str, ChatMessageRole]] = [
            ("the first request", "user"),
            ("the first response", "assistant"),
            ("a later request", "user"),
            ("a later response", "assistant"),
        ]
        for value, role in turns:
            sqlite_instance.add_message_to_memory(
                request=MessagePiece(role=role, original_value=value, conversation_id=conversation_id).to_message()
            )
        first_response = sqlite_instance.get_message_pieces(conversation_id=conversation_id)[1].to_message()

        objective = extract_objective_from_previous_turn(message=first_response, memory=sqlite_instance)

        assert objective == "the first request"


@pytest.mark.usefixtures("patch_central_database")
class TestInHandMessages:
    """A message already in hand is scored as it stands, not re-acquired."""

    async def test_score_message_async_does_not_read_memory(self):
        resolver = MagicMock(spec=MessageScorableResolver)
        scorer = RecordingScorer(message_resolver=resolver)
        message = _assistant_message("in hand")

        await scorer.score_message_async(message=message)

        resolver.resolve.assert_not_called()
        assert scorer.scored_messages == [message]

    async def test_score_message_async_does_not_dispatch_ephemeral_unreadable_error(self):
        scorer = RecordingScorer()
        message = MessagePiece(
            role="assistant",
            original_value="",
            original_value_data_type="error",
            response_error="unknown",
        ).to_message()
        message.set_response_not_in_memory()

        scores = await scorer.score_message_async(message=message)

        assert len(scores) == 1
        assert scores[0].is_undetermined
        assert scorer.scored_messages == []

    async def test_score_message_async_detects_unmarked_ephemeral_message(self):
        scorer = RecordingScorer()
        message = MessagePiece(role="assistant", original_value="not stored").to_message()

        score = (await scorer.score_message_async(message=message))[0]

        assert score.message_piece_id is None
        assert isinstance(score.scorable, ContentEntryScorable)

    async def test_score_message_async_does_not_anchor_unmarked_ephemeral_multipart_message(self):
        scorer = RecordingScorer()
        message = Message(
            message_pieces=[
                MessagePiece(role="assistant", original_value="first"),
                MessagePiece(role="assistant", original_value="second"),
            ]
        )

        score = (await scorer.score_message_async(message=message))[0]

        assert score.message_piece_id is None
        assert score.scorable is None

    async def test_score_message_async_applies_declared_roles(self):
        scorer = RecordingScorer(supported_roles=["user"])

        scores = await scorer.score_message_async(message=_assistant_message())

        assert scores == []


@pytest.mark.usefixtures("patch_central_database")
class TestConditionRouting:
    """An expectation is a routing envelope, so a condition is consumed or refused."""

    async def test_matches_objective_reaches_a_message_scorer(self):
        scorer = RecordingScorer(is_objective_required=True)

        scores = await scorer.score_async(
            scorable=MessageScorable.from_message(_assistant_message()),
            expectation=ScoringExpectation(objective="an objective", conditions=(MatchesObjective(),)),
        )

        assert len(scores) == 1

    async def test_matches_objective_without_an_objective_raises(self):
        scorer = RecordingScorer(is_objective_required=True)

        with pytest.raises(ValueError, match="MatchesObjective requires"):
            await scorer.score_async(
                scorable=MessageScorable.from_message(_assistant_message()),
                expectation=ScoringExpectation(conditions=(MatchesObjective(),)),
            )

    async def test_unconsumed_condition_raises_instead_of_being_dropped(self):
        @dataclasses.dataclass(frozen=True)
        class UnroutedCondition(Condition):
            pass

        scorer = RecordingScorer()

        with pytest.raises(ValueError, match="does not match the condition"):
            await scorer.score_async(
                scorable=MessageScorable.from_message(_assistant_message()),
                expectation=ScoringExpectation(conditions=(UnroutedCondition(),)),
            )

    async def test_two_conditions_of_one_type_raise(self):
        scorer = RecordingScorer(is_objective_required=True)

        with pytest.raises(ValueError, match="at most one condition"):
            await scorer.score_async(
                scorable=MessageScorable.from_message(_assistant_message()),
                expectation=ScoringExpectation(
                    objective="an objective",
                    conditions=(MatchesObjective(), MatchesObjective()),
                ),
            )

    def test_only_objective_required_scorers_match_objective(self):
        contextual_scorer = RecordingScorer()
        objective_scorer = RecordingScorer(is_objective_required=True)

        assert contextual_scorer.matched_conditions() == frozenset()
        assert contextual_scorer.required_conditions() == frozenset()
        assert objective_scorer.matched_conditions() == frozenset({MatchesObjective})
        assert objective_scorer.required_conditions() == frozenset({MatchesObjective})
