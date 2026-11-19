# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.models import (
    MessageScorable,
    ProgramExecutionScorable,
    ScoringExpectation,
    ToolCallRequirement,
    ToolsCalled,
)
from pyrit.prompt_target import PromptTarget
from pyrit.score import (
    AmbiguousConditionError,
    CompatibleScorers,
    ConflictingScorerRequirementsError,
    FloatScaleScorer,
    MessageToolCallScorer,
    MissingConditionError,
    NoCompatibleScorerError,
    ScorerCandidate,
    ScorerMismatchKind,
    ScorerRequirements,
    SelfAskTrueFalseScorer,
    SubStringScorer,
    TrueFalseQuestion,
    UnsupportedConditionError,
)

pytestmark = pytest.mark.usefixtures("patch_central_database")

CRISIS = "psychosocial.imminent_crisis"
THERAPIST = "psychosocial.licensed_therapist"


def _tools(*names: str) -> ScoringExpectation:
    return ScoringExpectation(conditions=(ToolsCalled(tools=tuple(ToolCallRequirement(name=n) for n in names)),))


def _substring(text: str = "x") -> SubStringScorer:
    return SubStringScorer(substring=text, categories=["test"])


# --- requirements ---


def test_combine_merges_evaluation_and_feedback():
    combined = ScorerRequirements.combine(
        ScorerRequirements(evaluation=CRISIS),
        None,
        ScorerRequirements(preserves_float_scale_value=True),
        ScorerRequirements(evaluation=CRISIS),
    )
    assert combined == ScorerRequirements(evaluation=CRISIS, preserves_float_scale_value=True)


def test_combine_with_no_owners_adds_no_constraint():
    assert ScorerRequirements.combine() == ScorerRequirements()


def test_combine_rejects_conflicting_evaluations():
    with pytest.raises(ConflictingScorerRequirementsError, match="cannot both decide success"):
        ScorerRequirements.combine(ScorerRequirements(evaluation=CRISIS), ScorerRequirements(evaluation=THERAPIST))


@pytest.mark.parametrize("key", ["", "   ", " padded"])
def test_evaluation_keys_must_be_trimmed_and_non_empty(key):
    with pytest.raises(ValueError, match="evaluation"):
        ScorerRequirements(evaluation=key)
    with pytest.raises(ValueError, match="evaluation"):
        ScorerCandidate(scorer=_substring(), evaluation=key)


def test_candidate_requires_true_false_scorer():
    with pytest.raises(TypeError, match="TrueFalseScorer"):
        ScorerCandidate(scorer=MagicMock(spec=FloatScaleScorer))


# --- pool construction ---


def test_bare_scorer_becomes_untagged_candidate():
    scorer = _substring()
    pool = CompatibleScorers(candidates=[scorer])
    assert pool.candidates == (ScorerCandidate(scorer=scorer),)
    assert pool.evaluations == (None,)
    assert CompatibleScorers.from_scorer(scorer).candidates == pool.candidates


def test_pool_rejects_empty_and_repeated_candidates():
    scorer = _substring()
    with pytest.raises(ValueError, match="at least one"):
        CompatibleScorers(candidates=[])
    with pytest.raises(ValueError, match="more than once"):
        CompatibleScorers(candidates=[ScorerCandidate(scorer=scorer, evaluation=CRISIS)] * 2)


def test_same_scorer_may_answer_two_evaluations():
    scorer = _substring()
    pool = CompatibleScorers(
        candidates=[
            ScorerCandidate(scorer=scorer, evaluation=CRISIS),
            ScorerCandidate(scorer=scorer, evaluation=THERAPIST),
        ]
    )
    assert pool.evaluations == (CRISIS, THERAPIST)


# --- selection ---


@pytest.mark.parametrize("reverse", [False, True])
def test_required_evaluation_wins_regardless_of_order(reverse):
    therapist = ScorerCandidate(scorer=_substring("a"), evaluation=THERAPIST)
    crisis = ScorerCandidate(scorer=_substring("b"), evaluation=CRISIS)
    candidates = [crisis, therapist] if reverse else [therapist, crisis]

    selected = CompatibleScorers(candidates=candidates).select(
        requirements=ScorerRequirements(evaluation=CRISIS),
        scorable_type=MessageScorable,
        expectations=[None],
    )

    assert selected.candidate is crisis
    assert selected.evaluation == CRISIS
    assert selected.candidate_index == (0 if reverse else 1)


def test_order_breaks_ties_between_matching_candidates():
    first, second = _substring("a"), _substring("b")
    selected = CompatibleScorers(candidates=[first, second]).select(
        requirements=ScorerRequirements(), scorable_type=MessageScorable, expectations=[None]
    )
    assert selected.scorer is first
    assert selected.candidate_index == 0


def test_missing_evaluation_key_is_named_with_available_keys():
    pool = CompatibleScorers(
        candidates=[
            ScorerCandidate(scorer=_substring("a"), evaluation=THERAPIST),
            ScorerCandidate(scorer=_substring("b"), evaluation=CRISIS),
        ]
    )
    with pytest.raises(NoCompatibleScorerError) as excinfo:
        pool.select(
            requirements=ScorerRequirements(evaluation="psychosocial.imminent_crisi"),
            scorable_type=MessageScorable,
            expectations=[None],
        )
    message = str(excinfo.value)
    assert "No candidate is bound to the required evaluation 'psychosocial.imminent_crisi'" in message
    assert f"'{CRISIS}', '{THERAPIST}'" in message
    assert [m.kind for m in excinfo.value.mismatches] == [ScorerMismatchKind.EVALUATION] * 2


def test_evidence_mismatch_is_typed():
    with pytest.raises(NoCompatibleScorerError) as excinfo:
        CompatibleScorers(candidates=[_substring()]).select(
            requirements=ScorerRequirements(), scorable_type=ProgramExecutionScorable, expectations=[None]
        )
    (mismatch,) = excinfo.value.mismatches
    assert mismatch.kind is ScorerMismatchKind.EVIDENCE
    assert "ProgramExecutionScorable" in mismatch.detail


def test_unsupported_condition_skips_to_a_candidate_that_accepts_it():
    substring, tool_scorer = _substring(), MessageToolCallScorer()
    selected = CompatibleScorers(candidates=[substring, tool_scorer]).select(
        requirements=ScorerRequirements(), scorable_type=MessageScorable, expectations=[_tools("lookup")]
    )
    assert selected.scorer is tool_scorer
    with pytest.raises(NoCompatibleScorerError) as excinfo:
        CompatibleScorers(candidates=[substring]).select(
            requirements=ScorerRequirements(), scorable_type=MessageScorable, expectations=[_tools("lookup")]
        )
    (mismatch,) = excinfo.value.mismatches
    assert mismatch.kind is ScorerMismatchKind.CONDITION
    assert "ToolsCalled" in mismatch.detail
    assert "rejected by SubStringScorer" in mismatch.detail


def test_missing_condition_skips_to_next_candidate():
    tool_scorer, substring = MessageToolCallScorer(), _substring()
    selected = CompatibleScorers(candidates=[tool_scorer, substring]).select(
        requirements=ScorerRequirements(), scorable_type=MessageScorable, expectations=[ScoringExpectation()]
    )
    assert selected.scorer is substring
    with pytest.raises(NoCompatibleScorerError) as excinfo:
        CompatibleScorers(candidates=[tool_scorer]).select(
            requirements=ScorerRequirements(), scorable_type=MessageScorable, expectations=[ScoringExpectation()]
        )
    (mismatch,) = excinfo.value.mismatches
    assert mismatch.kind is ScorerMismatchKind.CONDITION
    assert "requires one ToolsCalled condition" in mismatch.detail


def test_candidate_must_accept_every_input_in_the_population():
    pool = CompatibleScorers(candidates=[_substring(), MessageToolCallScorer()])
    with pytest.raises(NoCompatibleScorerError) as excinfo:
        pool.select(
            requirements=ScorerRequirements(),
            scorable_type=MessageScorable,
            expectations=[ScoringExpectation(), _tools("lookup")],
        )
    assert [m.kind for m in excinfo.value.mismatches] == [ScorerMismatchKind.CONDITION] * 2


def test_float_feedback_requirement_is_checked():
    with pytest.raises(NoCompatibleScorerError) as excinfo:
        CompatibleScorers(candidates=[_substring()]).select(
            requirements=ScorerRequirements(preserves_float_scale_value=True),
            scorable_type=MessageScorable,
            expectations=[None],
        )
    assert excinfo.value.mismatches[0].kind is ScorerMismatchKind.FLOAT_FEEDBACK


def test_malformed_input_raises_before_any_candidate_is_checked():
    scorer = _substring()
    duplicate = ScoringExpectation(
        conditions=(
            ToolsCalled(tools=(ToolCallRequirement(name="a"),)),
            ToolsCalled(tools=(ToolCallRequirement(name="b"),)),
        )
    )
    with patch.object(type(scorer), "prepare_expectation") as prepare:
        with pytest.raises(AmbiguousConditionError, match="Input #1"):
            CompatibleScorers(candidates=[scorer]).select(
                requirements=ScorerRequirements(), scorable_type=MessageScorable, expectations=[None, duplicate]
            )
    prepare.assert_not_called()


def test_repeated_inputs_are_checked_once_per_candidate():
    scorer = MessageToolCallScorer()
    expectations = [_tools("lookup"), _tools("lookup"), _tools("lookup"), _tools("send")]
    original = MessageToolCallScorer.prepare_expectation
    with patch.object(MessageToolCallScorer, "prepare_expectation", autospec=True, side_effect=original) as prepare:
        selected = CompatibleScorers(candidates=[scorer]).select(
            requirements=ScorerRequirements(), scorable_type=MessageScorable, expectations=expectations
        )
    assert prepare.call_count == 2
    assert selected.scorer is scorer


def test_empty_population_selects_first_candidate_meeting_requirements():
    therapist = ScorerCandidate(scorer=_substring(), evaluation=THERAPIST)
    selected = CompatibleScorers(candidates=[therapist]).select(
        requirements=ScorerRequirements(evaluation=THERAPIST), scorable_type=MessageScorable, expectations=[]
    )
    assert selected.candidate is therapist


def test_a_population_without_an_evaluation_uses_an_unbound_candidate():
    unbound = _substring("u")
    pool = CompatibleScorers(candidates=[ScorerCandidate(scorer=_substring("c"), evaluation=CRISIS), unbound])
    selected = pool.select(requirements=ScorerRequirements(), scorable_type=MessageScorable, expectations=[None])
    assert selected.scorer is unbound
    assert selected.candidate_index == 1


def test_bound_candidates_do_not_judge_a_population_without_an_evaluation():
    pool = CompatibleScorers(candidates=[ScorerCandidate(scorer=_substring(), evaluation=CRISIS)])
    with pytest.raises(NoCompatibleScorerError, match="This population requires no evaluation") as excinfo:
        pool.select(requirements=ScorerRequirements(), scorable_type=MessageScorable, expectations=[None])
    (mismatch,) = excinfo.value.mismatches
    assert mismatch.kind is ScorerMismatchKind.EVALUATION
    assert "bound to 'psychosocial.imminent_crisis'; the population requires none" in mismatch.detail


@pytest.mark.parametrize("error", [RuntimeError, ValueError])
def test_unexpected_validation_errors_propagate(error):
    scorer = _substring()
    with patch.object(type(scorer), "prepare_expectation", side_effect=error("judge misconfigured")):
        with pytest.raises(error, match="judge misconfigured"):
            CompatibleScorers(candidates=[scorer, _substring("y")]).select(
                requirements=ScorerRequirements(), scorable_type=MessageScorable, expectations=[None]
            )


def test_selection_never_calls_a_judge_model():
    chat_target = MagicMock(spec=PromptTarget)
    chat_target.send_prompt_async = AsyncMock(side_effect=AssertionError("selection called a model"))
    judge = SelfAskTrueFalseScorer(
        chat_target=chat_target,
        system_prompt="static classifier prompt",
        question=TrueFalseQuestion(category="harm", true_description="harmful", false_description="not harmful"),
    )
    pool = CompatibleScorers(candidates=[ScorerCandidate(scorer=judge, evaluation=CRISIS)])

    selected = pool.select(
        requirements=ScorerRequirements(evaluation=CRISIS),
        scorable_type=MessageScorable,
        expectations=[ScoringExpectation(objective="o1"), ScoringExpectation(objective="o2")],
    )

    assert selected.scorer is judge
    chat_target.send_prompt_async.assert_not_called()


# --- identity and typed validation errors ---


def test_pool_identity_depends_on_order_and_bindings():
    a, b = _substring("a"), _substring("b")
    base = CompatibleScorers(candidates=[ScorerCandidate(scorer=a, evaluation=CRISIS), b]).get_identifier()
    same = CompatibleScorers(candidates=[ScorerCandidate(scorer=a, evaluation=CRISIS), b]).get_identifier()
    reordered = CompatibleScorers(candidates=[b, ScorerCandidate(scorer=a, evaluation=CRISIS)]).get_identifier()
    rebound = CompatibleScorers(candidates=[ScorerCandidate(scorer=a, evaluation=THERAPIST), b]).get_identifier()

    assert base.hash == same.hash
    assert len({base.hash, reordered.hash, rebound.hash}) == 3
    assert base.params["evaluations"] == [CRISIS, None]


def test_scorer_validation_raises_typed_errors_with_existing_messages():
    with pytest.raises(UnsupportedConditionError, match="SubStringScorer does not support condition"):
        _substring().prepare_expectation(expectation=_tools("lookup"))
    with pytest.raises(MissingConditionError, match="requires one ToolsCalled condition") as excinfo:
        MessageToolCallScorer().prepare_expectation(expectation=ScoringExpectation())
    assert isinstance(excinfo.value, ValueError)
    assert excinfo.value.scorer_name == "MessageToolCallScorer"
