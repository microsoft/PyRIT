# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Independent trial accounting uses synthetic fixture objectives, not benchmark data."""

from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack, RepeatedAttack, SequentialChildAttack
from pyrit.memory import CentralMemory
from pyrit.models import AttackOutcome, AttackResultSelection, AttackSeedGroup, Message, MessagePiece, SeedObjective
from pyrit.score import SelfAskRefusalScorer


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("num_trials", [0, -1, True, 1.5])
def test_trial_count_must_be_a_positive_integer(num_trials: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        RepeatedAttack.validate_constructor_parameters({"num_trials": num_trials})


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("num_trials", "successful_trials"), [(1, 1), (5, 0), (10, 1), (10, 3), (10, 10)])
async def test_exact_trials_fresh_conversations_and_persisted_metadata_async(
    num_trials: int, successful_trials: int
) -> None:
    target = MockPromptTarget()
    judge = MockPromptTarget(id="judge")
    scorer = SelfAskRefusalScorer(chat_target=judge)
    attack = PromptSendingAttack(
        objective_target=target, attack_scoring_config=AttackScoringConfig(objective_scorer=scorer)
    )
    repeated = RepeatedAttack(
        objective_target=target,
        child_attack=SequentialChildAttack(
            strategy=attack,
            seed_group=AttackSeedGroup(seeds=[SeedObjective(value="fixture-objective")]),
        ),
        **({"num_trials": num_trials} if num_trials != 5 else {}),
    )
    replies = [
        [
            MessagePiece(
                role="assistant",
                original_value='{"score_value": "'
                + ("True" if index < successful_trials else "False")
                + '", "description": "fixture", "rationale": "fixture rationale"}',
            ).to_message()
        ]
        for index in range(num_trials)
    ]
    with patch.object(judge, "send_prompt_async", AsyncMock(side_effect=replies)):
        result = await repeated.execute_async(objective="fixture-objective")

    assert len(target.prompt_sent) == num_trials
    assert result.outcome is (AttackOutcome.SUCCESS if successful_trials else AttackOutcome.FAILURE)
    summary = result.metadata["independent_trials"]
    assert summary["successful_trials"] == successful_trials
    assert summary["num_trials"] == summary["valid_trials"] == num_trials
    assert summary["attack_success_rate"] == successful_trials / num_trials
    assert [trial["trial_index"] for trial in summary["trials"]] == list(range(1, num_trials + 1))
    assert len({trial["conversation_id"] for trial in summary["trials"]}) == num_trials
    assert all(trial["score_rationale"] == "fixture rationale" for trial in summary["trials"])
    memory = CentralMemory.get_memory_instance()
    stored = await memory.get_attack_results_async(
        attack_result_ids=[result.attack_result_id, *result.child_attack_result_ids],
        result_selection=AttackResultSelection.ALL_RESULTS,
    )
    assert len(stored) == num_trials + 1
    parent = next(row for row in stored if row.attack_result_id == result.attack_result_id)
    assert parent.metadata["independent_trials"] == summary
    assert all(row.metadata["trial_index"] in range(1, num_trials + 1) for row in stored if row is not parent)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("num_errors", "later_success", "expected_outcome"),
    [
        (1, False, AttackOutcome.FAILURE),
        (1, True, AttackOutcome.SUCCESS),
        (2, False, AttackOutcome.FAILURE),
        (2, True, AttackOutcome.SUCCESS),
        (3, False, AttackOutcome.ERROR),
    ],
)
async def test_persisted_trial_error_does_not_skip_later_trials_async(
    num_errors: int, later_success: bool, expected_outcome: AttackOutcome
) -> None:
    target = MockPromptTarget()
    judge = MockPromptTarget(id="judge")
    scorer = SelfAskRefusalScorer(chat_target=judge)
    attack = PromptSendingAttack(
        objective_target=target, attack_scoring_config=AttackScoringConfig(objective_scorer=scorer)
    )
    repeated = RepeatedAttack(
        objective_target=target,
        child_attack=SequentialChildAttack(
            strategy=attack,
            seed_group=AttackSeedGroup(seeds=[SeedObjective(value="fixture-objective")]),
        ),
        num_trials=3,
    )
    replies = [
        [
            MessagePiece(
                role="assistant",
                original_value='{"score_value": "'
                + ("True" if later_success else "False")
                + '", "description": "fixture", "rationale": "fixture rationale"}',
            ).to_message()
        ]
        for _ in range(3 - num_errors)
    ]
    original_send = target._send_prompt_to_target_async
    send = AsyncMock(side_effect=[RuntimeError("fixture transport error")] * num_errors + [None] * (3 - num_errors))

    async def send_async(*, normalized_conversation: list[Message]) -> list[Message]:
        error_or_none = await send()
        assert error_or_none is None
        return await original_send(normalized_conversation=normalized_conversation)

    with (
        patch.object(target, "_send_prompt_to_target_async", send_async),
        patch.object(judge, "send_prompt_async", AsyncMock(side_effect=replies)),
    ):
        result = await repeated.execute_async(objective="fixture-objective")

    assert send.await_count == 3
    assert result.outcome is expected_outcome
    summary = result.metadata["independent_trials"]
    assert summary["attack_success_rate"] is None
    assert summary["valid_trials"] == 3 - num_errors
    expected_trial_outcome = "success" if later_success else "failure"
    assert [trial["outcome"] for trial in summary["trials"]] == ["error"] * num_errors + [expected_trial_outcome] * (
        3 - num_errors
    )
    assert len(set(result.child_attack_result_ids)) == 3
    stored = await CentralMemory.get_memory_instance().get_attack_results_async(
        attack_result_ids=[result.attack_result_id],
        result_selection=AttackResultSelection.ALL_RESULTS,
    )
    assert len(stored) == 1
    assert stored[0].outcome is expected_outcome


@pytest.mark.usefixtures("patch_central_database")
async def test_unscored_trials_remain_undetermined_without_a_measured_rate_async() -> None:
    target = MockPromptTarget()
    repeated = RepeatedAttack(
        objective_target=target,
        child_attack=SequentialChildAttack(
            strategy=PromptSendingAttack(objective_target=target),
            seed_group=AttackSeedGroup(seeds=[SeedObjective(value="fixture-objective")]),
        ),
        num_trials=2,
    )
    result = await repeated.execute_async(objective="fixture-objective")
    assert len(target.prompt_sent) == 2
    assert result.outcome is AttackOutcome.UNDETERMINED
    summary = result.metadata["independent_trials"]
    assert summary["valid_trials"] == 0
    assert summary["attack_success_rate"] is None
    assert all(trial["score_value"] is None for trial in summary["trials"])
