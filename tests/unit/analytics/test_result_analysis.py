# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Optional

import pytest

from pyrit.analytics.result_analysis import AttackStats, analyze_results
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import AttackOutcome, AttackResult


# helpers
def make_attack(
    outcome: AttackOutcome,
    attack_type: Optional[str] = "default",
    conversation_id: str = "conv-1",
) -> AttackResult:
    """
    Minimal valid AttackResult for analytics tests.
    """
    attack_identifier: Optional[ComponentIdentifier] = None
    if attack_type is not None:
        attack_identifier = ComponentIdentifier(class_name=attack_type, class_module="tests.unit.analytics")

    return AttackResult(
        conversation_id=conversation_id,
        objective="test objective",
        attack_identifier=attack_identifier,
        outcome=outcome,
    )


def test_analyze_results_empty_raises():
    with pytest.raises(ValueError):
        analyze_results([])


def test_analyze_results_raises_on_invalid_object():
    with pytest.raises(TypeError):
        analyze_results(["not-an-AttackResult"])


@pytest.mark.parametrize(
    "outcomes, expected_successes, expected_failures, expected_undetermined, expected_rate",
    [
        # all successes
        ([AttackOutcome.SUCCESS, AttackOutcome.SUCCESS], 2, 0, 0, 1.0),
        # all failures
        ([AttackOutcome.FAILURE, AttackOutcome.FAILURE], 0, 2, 0, 0.0),
        # mixed decided
        ([AttackOutcome.SUCCESS, AttackOutcome.FAILURE], 1, 1, 0, 0.5),
        # include undetermined (excluded from denominator)
        ([AttackOutcome.SUCCESS, AttackOutcome.UNDETERMINED], 1, 0, 1, 1.0),
        ([AttackOutcome.FAILURE, AttackOutcome.UNDETERMINED], 0, 1, 1, 0.0),
        # multiple with undetermined
        (
            [AttackOutcome.SUCCESS, AttackOutcome.FAILURE, AttackOutcome.UNDETERMINED],
            1,
            1,
            1,
            0.5,
        ),
    ],
)
def test_overall_success_rate_parametrized(
    outcomes, expected_successes, expected_failures, expected_undetermined, expected_rate
):
    attacks = [make_attack(o) for o in outcomes]
    result = analyze_results(attacks)

    assert isinstance(result["Overall"], AttackStats)
    overall = result["Overall"]
    assert overall.successes == expected_successes
    assert overall.failures == expected_failures
    assert overall.undetermined == expected_undetermined
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
