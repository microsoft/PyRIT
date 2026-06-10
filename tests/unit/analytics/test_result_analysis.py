# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from pyrit.analytics.result_analysis import (
    AttackStats,
    _objective_target_eval_hash_for,
    analyze_results,
    get_cached_results_for_technique,
)
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    IdentifierFilter,
    IdentifierType,
    ObjectiveTargetEvaluationIdentifier,
    build_atomic_attack_identifier,
)


# helpers
def make_attack(
    outcome: AttackOutcome,
    attack_type: str | None = "default",
    conversation_id: str = "conv-1",
) -> AttackResult:
    """
    Minimal valid AttackResult for analytics tests.
    """
    atomic_attack_identifier: ComponentIdentifier | None = None
    if attack_type is not None:
        attack_identifier = ComponentIdentifier(class_name=attack_type, class_module="tests.unit.analytics")
        atomic_attack_identifier = build_atomic_attack_identifier(attack_identifier=attack_identifier)

    return AttackResult(
        conversation_id=conversation_id,
        objective="test objective",
        atomic_attack_identifier=atomic_attack_identifier,
        outcome=outcome,
    )


def test_analyze_results_empty_raises():
    with pytest.raises(ValueError):
        analyze_results([])


def test_analyze_results_raises_on_invalid_object():
    with pytest.raises(TypeError):
        analyze_results(["not-an-AttackResult"])


@pytest.mark.parametrize(
    "outcomes, expected_successes, expected_failures, expected_undetermined, expected_errors, expected_rate",
    [
        # all successes
        ([AttackOutcome.SUCCESS, AttackOutcome.SUCCESS], 2, 0, 0, 0, 1.0),
        # all failures
        ([AttackOutcome.FAILURE, AttackOutcome.FAILURE], 0, 2, 0, 0, 0.0),
        # mixed decided
        ([AttackOutcome.SUCCESS, AttackOutcome.FAILURE], 1, 1, 0, 0, 0.5),
        # include undetermined (excluded from denominator)
        ([AttackOutcome.SUCCESS, AttackOutcome.UNDETERMINED], 1, 0, 1, 0, 1.0),
        ([AttackOutcome.FAILURE, AttackOutcome.UNDETERMINED], 0, 1, 1, 0, 0.0),
        # multiple with undetermined
        (
            [AttackOutcome.SUCCESS, AttackOutcome.FAILURE, AttackOutcome.UNDETERMINED],
            1,
            1,
            1,
            0,
            0.5,
        ),
        # error excluded from denominator (like undetermined)
        ([AttackOutcome.SUCCESS, AttackOutcome.ERROR], 1, 0, 0, 1, 1.0),
        ([AttackOutcome.FAILURE, AttackOutcome.ERROR], 0, 1, 0, 1, 0.0),
        # all errors
        ([AttackOutcome.ERROR, AttackOutcome.ERROR], 0, 0, 0, 2, None),
        # mixed with error and undetermined
        (
            [AttackOutcome.SUCCESS, AttackOutcome.FAILURE, AttackOutcome.ERROR, AttackOutcome.UNDETERMINED],
            1,
            1,
            1,
            1,
            0.5,
        ),
    ],
)
def test_overall_success_rate_parametrized(
    outcomes, expected_successes, expected_failures, expected_undetermined, expected_errors, expected_rate
):
    attacks = [make_attack(o) for o in outcomes]
    result = analyze_results(attacks)

    assert isinstance(result["Overall"], AttackStats)
    overall = result["Overall"]
    assert overall.successes == expected_successes
    assert overall.failures == expected_failures
    assert overall.undetermined == expected_undetermined
    assert overall.errors == expected_errors
    assert overall.total_decided == expected_successes + expected_failures
    assert overall.success_rate == expected_rate


@pytest.mark.parametrize(
    "items, type_key, exp_succ, exp_fail, exp_und, exp_rate",
    [
        # single type, mixed decided + undetermined
        (
            [
                (AttackOutcome.SUCCESS, "crescendo"),
                (AttackOutcome.FAILURE, "crescendo"),
                (AttackOutcome.UNDETERMINED, "crescendo"),
            ],
            "crescendo",
            1,
            1,
            1,
            0.5,
        ),
        # two types with different balances
        (
            [
                (AttackOutcome.SUCCESS, "crescendo"),
                (AttackOutcome.FAILURE, "crescendo"),
                (AttackOutcome.SUCCESS, "red_teaming"),
                (AttackOutcome.FAILURE, "red_teaming"),
                (AttackOutcome.SUCCESS, "red_teaming"),
            ],
            "red_teaming",
            2,
            1,
            0,
            2 / 3,
        ),
        # unknown type fallback (missing "type" key)
        (
            [
                (AttackOutcome.FAILURE, None),
                (AttackOutcome.UNDETERMINED, None),
                (AttackOutcome.SUCCESS, None),
            ],
            "unknown",
            1,
            1,
            1,
            0.5,
        ),
    ],
)
def test_group_by_attack_type_parametrized(items, type_key, exp_succ, exp_fail, exp_und, exp_rate):
    attacks = [make_attack(outcome=o, attack_type=t) for (o, t) in items]
    result = analyze_results(attacks)

    assert type_key in result["By_attack_identifier"]
    stats = result["By_attack_identifier"][type_key]
    assert isinstance(stats, AttackStats)
    assert stats.successes == exp_succ
    assert stats.failures == exp_fail
    assert stats.undetermined == exp_und
    assert stats.total_decided == exp_succ + exp_fail
    assert stats.success_rate == exp_rate


# ---------------------------------------------------------------------------
# get_cached_results_for_technique tests
# ---------------------------------------------------------------------------


def _make_target_component(*, model_name: str = "gpt-4o", temperature: float = 0.7) -> ComponentIdentifier:
    return ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target.openai.openai_chat_target",
        params={
            "underlying_model_name": model_name,
            "temperature": temperature,
            "top_p": 1.0,
            "endpoint": "https://east.example.com",
        },
    )


def _make_attack_with_target(
    target: ComponentIdentifier,
    *,
    outcome: AttackOutcome = AttackOutcome.SUCCESS,
    timestamp: datetime | None = None,
) -> AttackResult:
    technique = ComponentIdentifier(
        class_name="PromptSendingAttack",
        class_module="pyrit.executor.attack.single_turn.prompt_sending",
        children={"objective_target": target},
    )
    atomic = ComponentIdentifier(
        class_name="AtomicAttack",
        class_module="pyrit.scenario.core.atomic_attack",
        children={"attack_technique": technique},
    )
    return AttackResult(
        conversation_id="conv-1",
        objective="test objective",
        atomic_attack_identifier=atomic,
        outcome=outcome,
        timestamp=timestamp or datetime.now(timezone.utc),
    )


def test_get_cached_results_for_technique_returns_matching():
    target = _make_target_component()
    expected_hash = ObjectiveTargetEvaluationIdentifier(target).eval_hash
    matching = _make_attack_with_target(target)

    memory = MagicMock(spec=MemoryInterface)
    memory.get_attack_results.return_value = [matching]

    results = get_cached_results_for_technique(
        memory,
        technique_eval_hash="tech-hash",
        objective_target_eval_hash=expected_hash,
    )

    assert results == [matching]


def test_get_cached_results_for_technique_filters_out_target_mismatches():
    target_match = _make_target_component(model_name="gpt-4o")
    target_other = _make_target_component(model_name="gpt-4o-mini")
    expected_hash = ObjectiveTargetEvaluationIdentifier(target_match).eval_hash

    memory = MagicMock(spec=MemoryInterface)
    memory.get_attack_results.return_value = [
        _make_attack_with_target(target_other),
        _make_attack_with_target(target_match),
        _make_attack_with_target(target_other),
    ]

    results = get_cached_results_for_technique(
        memory,
        technique_eval_hash="tech-hash",
        objective_target_eval_hash=expected_hash,
    )

    assert len(results) == 1
    assert results[0].atomic_attack_identifier == _make_attack_with_target(target_match).atomic_attack_identifier


def test_get_cached_results_for_technique_returns_empty_when_no_candidates():
    memory = MagicMock(spec=MemoryInterface)
    memory.get_attack_results.return_value = []

    results = get_cached_results_for_technique(
        memory,
        technique_eval_hash="tech-hash",
        objective_target_eval_hash="target-hash",
    )

    assert results == []


def test_get_cached_results_for_technique_sorts_newest_first():
    target = _make_target_component()
    expected_hash = ObjectiveTargetEvaluationIdentifier(target).eval_hash
    now = datetime.now(timezone.utc)
    older = _make_attack_with_target(target, timestamp=now - timedelta(hours=2))
    middle = _make_attack_with_target(target, timestamp=now - timedelta(hours=1))
    newest = _make_attack_with_target(target, timestamp=now)

    memory = MagicMock(spec=MemoryInterface)
    memory.get_attack_results.return_value = [older, newest, middle]

    results = get_cached_results_for_technique(
        memory,
        technique_eval_hash="tech-hash",
        objective_target_eval_hash=expected_hash,
    )

    assert [r.timestamp for r in results] == [newest.timestamp, middle.timestamp, older.timestamp]


def test_get_cached_results_for_technique_builds_default_sql_filter():
    memory = MagicMock(spec=MemoryInterface)
    memory.get_attack_results.return_value = []

    get_cached_results_for_technique(
        memory,
        technique_eval_hash="tech-hash-xyz",
        objective_target_eval_hash="target-hash",
    )

    memory.get_attack_results.assert_called_once()
    filters = memory.get_attack_results.call_args.kwargs["identifier_filters"]
    assert len(filters) == 1
    sole = filters[0]
    assert sole.identifier_type == IdentifierType.ATTACK
    assert sole.property_path == "$.eval_hash"
    assert sole.value == "tech-hash-xyz"


def test_get_cached_results_for_technique_appends_additional_filters():
    memory = MagicMock(spec=MemoryInterface)
    memory.get_attack_results.return_value = []
    extra = IdentifierFilter(
        identifier_type=IdentifierType.ATTACK,
        property_path="$.children.attack_technique.children.attack.class_name",
        value="PromptSendingAttack",
    )

    get_cached_results_for_technique(
        memory,
        technique_eval_hash="tech-hash",
        objective_target_eval_hash="target-hash",
        additional_filters=[extra],
    )

    filters = memory.get_attack_results.call_args.kwargs["identifier_filters"]
    assert len(filters) == 2
    assert filters[1] is extra


def test_get_cached_results_for_technique_skips_results_without_identifier():
    """Results with no atomic_attack_identifier are ignored, not raised on."""
    target = _make_target_component()
    expected_hash = ObjectiveTargetEvaluationIdentifier(target).eval_hash
    matching = _make_attack_with_target(target)
    orphan = AttackResult(
        conversation_id="orphan",
        objective="o",
        atomic_attack_identifier=None,
        outcome=AttackOutcome.SUCCESS,
    )

    memory = MagicMock(spec=MemoryInterface)
    memory.get_attack_results.return_value = [orphan, matching]

    results = get_cached_results_for_technique(
        memory,
        technique_eval_hash="tech-hash",
        objective_target_eval_hash=expected_hash,
    )

    assert results == [matching]


def test_objective_target_eval_hash_for_missing_attack_technique_returns_none():
    """Helper returns None when the identifier tree is missing attack_technique."""
    atomic_only = ComponentIdentifier(
        class_name="AtomicAttack",
        class_module="pyrit.scenario.core.atomic_attack",
    )
    result = AttackResult(
        conversation_id="c",
        objective="o",
        atomic_attack_identifier=atomic_only,
        outcome=AttackOutcome.SUCCESS,
    )
    assert _objective_target_eval_hash_for(result) is None


def test_objective_target_eval_hash_for_missing_objective_target_returns_none():
    """Helper returns None when attack_technique has no objective_target child."""
    technique = ComponentIdentifier(
        class_name="PromptSendingAttack",
        class_module="pyrit.executor.attack.single_turn.prompt_sending",
    )
    atomic = ComponentIdentifier(
        class_name="AtomicAttack",
        class_module="pyrit.scenario.core.atomic_attack",
        children={"attack_technique": technique},
    )
    result = AttackResult(
        conversation_id="c",
        objective="o",
        atomic_attack_identifier=atomic,
        outcome=AttackOutcome.SUCCESS,
    )
    assert _objective_target_eval_hash_for(result) is None
