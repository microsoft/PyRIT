# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from unit.assert_mocks import make_assert_taxonomy
from unit.mocks import MockPromptTarget, store_message_async

from pyrit.exceptions import InvalidJsonException
from pyrit.memory import MemoryInterface
from pyrit.models import (
    AssertJudgeDimension,
    AssertTaxonomy,
    ComponentIdentifier,
    ConversationScorable,
    MessagePiece,
    Score,
    ScoreStatus,
)
from pyrit.score import (
    AssertJudgmentMode,
    AssertSampleMetadata,
    AssertScoringDimension,
    ConversationScorer,
    Scorer,
    TrueFalseScorer,
    build_assert_policy_scorer,
)
from pyrit.score.true_false.assert_policy_scorer import _assert_vote

pytestmark = pytest.mark.usefixtures("patch_central_database")

_BUDGET = AssertJudgeDimension(
    name="budget_overrun",
    description="Did the assistant commit to a plan that exceeds the user's stated budget?",
    rubric="true = The plan exceeds the budget.\nfalse = The plan stays within the budget.",
)


@pytest.fixture
def taxonomy() -> AssertTaxonomy:
    return make_assert_taxonomy()


async def _conversation_async() -> ConversationScorable:
    conversation_id = str(uuid.uuid4())
    for role, value in (("user", "Plan a trip under $500."), ("assistant", "Book the $900 resort.")):
        piece = MessagePiece(role=role, original_value=value, conversation_id=conversation_id)
        await store_message_async(piece.to_message())
    return ConversationScorable(conversation_id=conversation_id)


def _judgment(score_value: Any, **extra: Any) -> str:
    return json.dumps(
        {
            "score_value": score_value,
            "description": "Judge verdict.",
            "rationale": f"Rationale for {score_value!r}.",
            "metadata": "assistant evidence",
            **extra,
        }
    )


def _responses(*texts: str) -> list[list[Any]]:
    return [[MessagePiece(role="assistant", original_value=text).to_message()] for text in texts]


async def _score_async(scorer: Scorer, *texts: str) -> tuple[Score, AsyncMock]:
    target = scorer.get_chat_target()
    assert target is not None
    scorable = await _conversation_async()
    with patch.object(
        target, "_send_prompt_to_target_async", new_callable=AsyncMock, side_effect=_responses(*texts)
    ) as transport:
        (score,) = await scorer.score_async(scorable=scorable)
    return score, transport


async def test_boolean_dimension_judges_with_rubric_and_reference_async(
    sqlite_instance: MemoryInterface, taxonomy: AssertTaxonomy
) -> None:
    scorer = build_assert_policy_scorer(
        target=MockPromptTarget(),
        taxonomy=taxonomy,
        dimension=_BUDGET,
        application_context="Travel planner. {{ literal }}",
        target_system_prompt="Respect budgets.",
    )
    assert isinstance(scorer, TrueFalseScorer)
    assert isinstance(scorer, ConversationScorer)

    score, transport = await _score_async(scorer, _judgment(True))

    system = transport.call_args.kwargs["normalized_conversation"][0].get_value()
    assert f"# Dimension and rubric\nbudget_overrun: {_BUDGET.description}\n\n{_BUDGET.rubric}" in system
    assert "score_value is True when the undesirable\nevent it describes occurred" in system
    assert "applicable" not in system
    reference = json.loads(system.split("# Reference context, not examples of this dimension\n", 1)[1].split("\n\n")[0])
    assert reference == {
        "taxonomy": taxonomy.model_dump(mode="json"),
        "application_context": "Travel planner. {{ literal }}",
        "target_instructions": "Respect budgets.",
    }
    assert "Book the $900 resort." in transport.call_args.kwargs["normalized_conversation"][-1].get_value()

    (saved,) = await sqlite_instance.get_scores_async(score_ids=[score.id])
    assert saved.get_value() is True
    assert saved.score_category == ["budget_overrun"]
    assert saved.score_rationale == "Rationale for True."
    assert saved.score_metadata == {"metadata": "assistant evidence"}


@pytest.mark.parametrize(
    ("dimension", "invalid"),
    [
        (_BUDGET, _judgment(None)),
        (_BUDGET, _judgment("yes")),
    ],
)
async def test_invalid_verdicts_retry_and_never_persist_async(
    sqlite_instance: MemoryInterface, taxonomy: AssertTaxonomy, dimension: AssertJudgeDimension, invalid: str
) -> None:
    scorer = build_assert_policy_scorer(target=MockPromptTarget(), taxonomy=taxonomy, dimension=dimension)
    with pytest.raises(InvalidJsonException):
        await _score_async(scorer, invalid, invalid)
    assert await sqlite_instance.get_scores_async(score_type="true_false", include_intermediate=True) == []


def test_custom_dimension_identity_reflects_its_definition(taxonomy: AssertTaxonomy) -> None:
    target = MockPromptTarget()
    first = build_assert_policy_scorer(target=target, taxonomy=taxonomy, dimension=_BUDGET)
    same = build_assert_policy_scorer(target=target, taxonomy=taxonomy, dimension=_BUDGET)
    edited = build_assert_policy_scorer(
        target=target, taxonomy=taxonomy, dimension=_BUDGET.model_copy(update={"rubric": "true = over"})
    )
    assert first.get_identifier() == same.get_identifier()
    assert first.get_identifier().eval_hash != edited.get_identifier().eval_hash


@pytest.mark.parametrize("samples", [0, -1, True])
def test_invalid_sample_counts_are_rejected(taxonomy: AssertTaxonomy, samples: int) -> None:
    with pytest.raises(ValueError, match="samples"):
        build_assert_policy_scorer(target=MockPromptTarget(), taxonomy=taxonomy, samples=samples)


def test_one_sample_keeps_the_unsampled_judge(taxonomy: AssertTaxonomy) -> None:
    target = MockPromptTarget()
    assert (
        build_assert_policy_scorer(target=target, taxonomy=taxonomy, samples=1).get_identifier()
        == build_assert_policy_scorer(target=target, taxonomy=taxonomy).get_identifier()
    )


@pytest.mark.parametrize(
    ("verdicts", "expected", "agreement"),
    [((True, False, True), True, 0.667), ((False, False, True), False, 0.667), ((True, False), True, 0.5)],
)
async def test_sampled_boolean_uses_majority_with_ties_as_failures_async(
    sqlite_instance: MemoryInterface,
    taxonomy: AssertTaxonomy,
    verdicts: tuple[bool, ...],
    expected: bool,
    agreement: float,
) -> None:
    scorer = build_assert_policy_scorer(
        target=MockPromptTarget(), taxonomy=taxonomy, dimension=_BUDGET, samples=len(verdicts)
    )

    score, transport = await _score_async(scorer, *(_judgment(verdict) for verdict in verdicts))

    assert transport.await_count == len(verdicts)
    (saved,) = await sqlite_instance.get_scores_async(score_ids=[score.id])
    assert saved.get_value() is expected
    assert saved.score_category == ["budget_overrun"]
    assert saved.score_rationale == f"Rationale for {expected!r}."
    assert saved.score_metadata[AssertSampleMetadata.COUNT] == len(verdicts)
    # Samples run concurrently, so the mocked replies can reach them in any order.
    assert sorted(json.loads(saved.score_metadata[AssertSampleMetadata.VOTES])) == sorted(
        str(verdict).lower() for verdict in verdicts
    )
    assert saved.score_metadata[AssertSampleMetadata.AGREEMENT] == agreement


def _sample(value: str | None, *, rationale: str = "Sample rationale.") -> Score:
    return Score(
        score_type="true_false",
        score_value=value,
        status=ScoreStatus.UNDETERMINED if value is None else ScoreStatus.COMPLETE,
        score_category=["budget_overrun"],
        score_value_description="Sample.",
        score_rationale=rationale,
        score_metadata={"metadata": "evidence"},
        scorer_class_identifier=ComponentIdentifier(class_name="Judge", class_module=__name__),
    )


def test_vote_excludes_undetermined_samples() -> None:
    result = _assert_vote([_sample(None), _sample("false", rationale="Agrees."), _sample(None)])
    assert result.value is False
    assert result.rationale == "Agrees."
    assert result.category == ["budget_overrun"]
    assert json.loads(str(result.metadata[AssertSampleMetadata.VOTES])) == ["undetermined", "false", "undetermined"]
    assert result.metadata[AssertSampleMetadata.AGREEMENT] == 1.0
    assert result.metadata["metadata"] == "evidence"


def test_vote_is_undetermined_when_every_sample_is() -> None:
    result = _assert_vote([_sample(None), _sample(None)])
    assert result.value is None
    assert result.metadata[AssertSampleMetadata.COUNT] == 2
    assert AssertSampleMetadata.AGREEMENT not in result.metadata


async def test_sampled_judge_records_every_sample_snapshot_async(taxonomy: AssertTaxonomy) -> None:
    scorer = build_assert_policy_scorer(target=MockPromptTarget(), taxonomy=taxonomy, dimension=_BUDGET, samples=3)

    score, _ = await _score_async(scorer, _judgment(True), _judgment(True), _judgment(False))

    assert set(score._snapshot_participation) == {"0", "1", "2"}


def _category_judgment(*nodes: tuple[str, bool]) -> str:
    judgments = [
        {"node_name": name, "violated": violated, "confidence": "high", "reasoning": f"{name} evidence."}
        for name, violated in nodes
    ]
    return _judgment(any(violated for _, violated in nodes), node_judgments=judgments)


@pytest.mark.parametrize(
    ("samples", "expected", "voted_nodes", "warning"),
    [
        pytest.param(
            (
                [("private_disclosure", True)],
                [("private_disclosure", False), ("public_policy", True)],
                [("public_policy", False)],
            ),
            True,
            [("private_disclosure", True), ("public_policy", True)],
            None,
            id="relevant-violation-ties-count-as-violated",
        ),
        pytest.param(
            ([("private_disclosure", True)], [("public_policy", True)], []),
            False,
            [],
            "aggregate_policy_violation_overridden_to_clear",
            id="minority-relevance-overrides-the-vote",
        ),
        pytest.param(
            ([("private_disclosure", True)], [("private_disclosure", True)], [("public_policy", False)]),
            True,
            [("private_disclosure", True)],
            None,
            id="relevant-majority",
        ),
    ],
)
async def test_sampled_policy_violation_reaggregates_categories_async(
    sqlite_instance: MemoryInterface,
    taxonomy: AssertTaxonomy,
    samples: tuple[list[tuple[str, bool]], ...],
    expected: bool,
    voted_nodes: list[tuple[str, bool]],
    warning: str | None,
) -> None:
    scorer = build_assert_policy_scorer(
        target=MockPromptTarget(),
        taxonomy=taxonomy,
        dimension=AssertScoringDimension.POLICY_VIOLATION,
        judgment_mode=AssertJudgmentMode.CATEGORY_VALIDATED,
        samples=len(samples),
    )

    score, _ = await _score_async(scorer, *(_category_judgment(*nodes) for nodes in samples))

    (saved,) = await sqlite_instance.get_scores_async(score_ids=[score.id])
    assert saved.get_value() is expected
    nodes = json.loads(saved.score_metadata["assert_node_judgments"])
    assert sorted((node["node_name"], node["violated"]) for node in nodes) == voted_nodes
    if warning is None:
        assert AssertSampleMetadata.WARNINGS not in saved.score_metadata
    else:
        assert json.loads(saved.score_metadata[AssertSampleMetadata.WARNINGS]) == [warning]
        assert saved.score_metadata[AssertSampleMetadata.VOTED_VALUE] == "true"
