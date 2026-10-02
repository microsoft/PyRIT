# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.executor.attack import (
    AttackExecutor,
    AttackParameters,
    AttackScoringConfig,
    PromptSendingAttack,
    SingleTurnAttackContext,
)
from pyrit.executor.attack.compound import SequentialAttack, SequentialChildAttack
from pyrit.executor.attack.multi_turn import simulated_conversation
from pyrit.executor.attack.multi_turn.multi_prompt_sending import (
    MultiPromptSendingAttack,
    MultiPromptSendingAttackParameters,
)
from pyrit.executor.attack.multi_turn.simulated_conversation import SimulatedConversationResult
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    AnswerMatches,
    AttackOutcome,
    AttackSeedGroup,
    MatchesObjective,
    ScoringExpectation,
    SeedDataset,
    SeedGroup,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
    ToolCallRequirement,
    ToolsCalled,
    TraceScorable,
    TraceSpan,
)
from pyrit.score import InMemoryTraceClient, OtelToolCallScorer, OtelTraceSource, QuestionAnswerScorer, Scorer


def _group(answer: str = "default") -> AttackSeedGroup:
    return AttackSeedGroup(
        seeds=[
            SeedObjective(
                value="Answer the question",
                conditions=(AnswerMatches(correct_answer=answer, correct_answer_label="7"),),
            ),
            SeedPrompt(value="Return one word"),
        ]
    )


@pytest.mark.usefixtures("patch_central_database")
class TestSeedExpectationTransport:
    async def test_tool_seed_round_trip_and_explicit_trace_replay_async(
        self, *, tmp_path: Path, sqlite_instance: SQLiteMemory
    ) -> None:
        path = tmp_path / "tool_expectations.yaml"
        await asyncio.to_thread(
            path.write_text,
            """\
name: tool_expectations
seeds:
  - seed_type: objective
    value: Read the file
    conditions:
      - condition_type: tools_called
        tools:
          - name: read_file
""",
            encoding="utf-8",
        )
        dataset = SeedDataset.from_yaml_file(path)
        await sqlite_instance.add_seeds_to_memory_async(seeds=dataset.seeds, added_by="test")
        [stored_group] = await sqlite_instance.get_seed_groups_async(dataset_name="tool_expectations")
        params = await AttackParameters.from_seed_group_async(seed_group=AttackSeedGroup(seeds=stored_group.seeds))
        assert params.expectation == ScoringExpectation(
            objective="Read the file", conditions=(ToolsCalled(tools=(ToolCallRequirement(name="read_file"),)),)
        )

        client = InMemoryTraceClient()
        now = datetime.now(tz=UTC)
        scope = TraceScorable(trace_ids=("1" * 32,))
        client.add_span(
            TraceSpan(
                trace_id=scope.trace_ids[0],
                span_id="2" * 16,
                start_time=now,
                end_time=now,
                attributes={"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "read_file"},
            )
        )
        client.mark_complete(trace_ids=scope.trace_ids)
        scorer = OtelToolCallScorer(source=OtelTraceSource(trace_client=client))
        Scorer.validate_expectation_for_scorers(scorers=[scorer], expectation=params.expectation)
        with pytest.raises(ValueError, match="ToolsCalled"):
            Scorer.validate_expectation_for_scorers(scorers=[scorer], expectation=None)
        [score] = await scorer.score_async(scorable=scope, expectation=params.expectation)
        assert score.get_value() is True
        [stored_score] = await sqlite_instance.get_scores_async(score_ids=[score.id])
        assert stored_score.scored_expectation == params.expectation
        [observation] = await sqlite_instance.get_observations_async(observation_ids=score.observation_ids)
        client.close()

        changed_seed = SeedObjective(
            value="Write the file", conditions=(ToolsCalled(tools=(ToolCallRequirement(name="write_file"),)),)
        )
        [replayed] = await scorer.score_observation_async(
            observation=observation, expectation=SeedGroup(seeds=[changed_seed]).scoring_expectation
        )
        assert replayed.get_value() is False
        assert replayed.observation_ids == score.observation_ids

    async def test_seed_criteria_reach_parameters_async(self) -> None:
        group = _group()

        params = await AttackParameters.from_seed_group_async(seed_group=group)

        assert params.expectation == group.scoring_expectation
        assert params.objective == group.objective.value
        assert params.next_message is not None
        assert params.next_message.get_value() == "Return one word"
        assert not params.next_message.get_piece().prompt_metadata

    async def test_multi_prompt_seed_criteria_reach_scoring_async(self) -> None:
        group = _group()
        params = await MultiPromptSendingAttackParameters.from_seed_group_async(seed_group=group)
        assert params.expectation == group.scoring_expectation
        target = MockPromptTarget()
        attack = MultiPromptSendingAttack(
            objective_target=target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=QuestionAnswerScorer()),
        )
        result = await attack.execute_async(
            objective=params.objective, user_messages=params.user_messages, expectation=params.expectation
        )
        assert result.outcome == AttackOutcome.SUCCESS
        assert result.automated_score is not None
        assert result.automated_score.scored_expectation == group.scoring_expectation

    @pytest.mark.parametrize("override", [None, ScoringExpectation(objective="replacement")])
    async def test_multi_prompt_explicit_expectation_override_async(self, override: ScoringExpectation | None) -> None:
        params = await MultiPromptSendingAttackParameters.from_seed_group_async(
            seed_group=_group(), expectation=override
        )
        assert params.expectation is override

    async def test_objective_override_keeps_authored_criteria_async(self) -> None:
        group = _group()
        params = await AttackParameters.from_seed_group_async(seed_group=group, objective="different attack objective")

        assert params.objective == "different attack objective"
        assert params.expectation == group.scoring_expectation

    @pytest.mark.parametrize("override", [None, ScoringExpectation(objective="replacement")])
    async def test_explicit_expectation_replaces_seed_criteria_async(self, override: ScoringExpectation | None) -> None:
        params = await AttackParameters.from_seed_group_async(seed_group=_group(), expectation=override)

        assert params.expectation is override
        if override is None:
            assert SingleTurnAttackContext(params=params).expectation == ScoringExpectation(
                objective="Answer the question"
            )

    async def test_condition_free_seed_keeps_objective_override_fallback_async(self) -> None:
        params = await AttackParameters.from_seed_group_async(
            seed_group=AttackSeedGroup(seeds=[SeedObjective(value="original")]), objective="override"
        )

        assert params.expectation is None
        assert SingleTurnAttackContext(params=params).expectation == ScoringExpectation(objective="override")

    async def test_parameter_type_excluding_expectation_stays_supported_async(self) -> None:
        params_type = AttackParameters.excluding("expectation")
        params = await params_type.from_seed_group_async(seed_group=_group())

        assert not hasattr(params, "expectation")

    async def test_simulated_preparation_keeps_seed_criteria_separate_async(self) -> None:
        group = AttackSeedGroup(
            seeds=[
                _group().objective,
                SeedSimulatedConversation(
                    num_turns=1, adversarial_chat_system_prompt=SeedPrompt(value="Prepare a conversation.")
                ),
            ]
        )
        prepared = SimulatedConversationResult(
            seed_prompts=[SeedPrompt(value="prepared prompt")], related_conversations=frozenset()
        )
        with patch.object(
            simulated_conversation,
            "generate_simulated_conversation_async",
            new_callable=AsyncMock,
            return_value=prepared,
        ) as generate:
            params = await AttackParameters.from_seed_group_async(
                seed_group=group, adversarial_chat=MockPromptTarget(), objective_scorer=QuestionAnswerScorer()
            )

        assert params.expectation == group.scoring_expectation
        assert params.next_message is not None
        assert params.next_message.get_value() == "prepared prompt"
        assert "expectation" not in generate.call_args.kwargs
        assert generate.call_args.kwargs["objective"] == group.objective.value

    async def test_compound_uses_child_seed_criteria_async(self, sqlite_instance: SQLiteMemory) -> None:
        group = _group()
        target = MockPromptTarget()
        child = PromptSendingAttack(
            objective_target=target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=QuestionAnswerScorer()),
        )
        compound = SequentialAttack(
            objective_target=target, child_attacks=[SequentialChildAttack(strategy=child, seed_group=group)]
        )

        result = await compound.execute_async(objective="Parent objective")

        assert result.outcome == AttackOutcome.SUCCESS
        [score] = await sqlite_instance.get_scores_async(score_type="true_false")
        assert score.scored_expectation == group.scoring_expectation

    async def test_yaml_memory_attack_score_round_trip_async(
        self, tmp_path: Path, sqlite_instance: SQLiteMemory
    ) -> None:
        path = tmp_path / "questions.yaml"
        await asyncio.to_thread(
            path.write_text,
            """\
name: seed_expectation_transport
seeds:
  - seed_type: objective
    value: Answer the question
    prompt_group_alias: row
    conditions:
      - condition_type: answer_matches
        correct_answer: default
        correct_answer_label: "7"
  - value: Return one word
    prompt_group_alias: row
""",
            encoding="utf-8",
        )
        dataset = SeedDataset.from_yaml_file(path)
        await sqlite_instance.add_seeds_to_memory_async(seeds=dataset.seeds, added_by="test")
        [stored_group] = await sqlite_instance.get_seed_groups_async(dataset_name="seed_expectation_transport")
        group = AttackSeedGroup(seeds=stored_group.seeds)
        target = MockPromptTarget()
        attack = PromptSendingAttack(
            objective_target=target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=QuestionAnswerScorer()),
        )

        batch = await AttackExecutor().execute_attack_from_seed_groups_async(attack=attack, seed_groups=[group])

        [result] = batch.get_results()
        assert result.outcome == AttackOutcome.SUCCESS
        assert target.prompt_sent == ["Return one word"]
        [score] = await sqlite_instance.get_scores_async(score_type="true_false")
        assert score.scored_expectation == group.scoring_expectation
        assert score.get_value() is True
        assert result.last_response is not None
        assert "correct_answer" not in result.last_response.prompt_metadata
        assert "correct_answer_index" not in result.last_response.prompt_metadata
        assert "correct_answer_label" not in result.last_response.prompt_metadata

    async def test_shared_and_row_overrides_replace_seed_conditions_async(self) -> None:
        replacement = ScoringExpectation(conditions=(AnswerMatches(correct_answer="other", correct_answer_label="8"),))
        broadcast = ScoringExpectation(conditions=(AnswerMatches(correct_answer="default", correct_answer_label="7"),))
        attack = PromptSendingAttack(
            objective_target=MockPromptTarget(),
            attack_scoring_config=AttackScoringConfig(objective_scorer=QuestionAnswerScorer()),
        )

        batch = await AttackExecutor(max_concurrency=2).execute_attack_from_seed_groups_async(
            attack=attack,
            seed_groups=[_group("wrong"), _group("wrong")],
            expectation=broadcast,
            field_overrides=[{}, {"expectation": replacement}],
        )

        results = batch.get_results()
        assert [result.outcome for result in results] == [AttackOutcome.SUCCESS, AttackOutcome.FAILURE]
        for result, expected in zip(results, [broadcast, replacement], strict=True):
            assert result.automated_score is not None
            assert result.automated_score.scored_expectation == expected.model_copy(
                update={"objective": "Answer the question"}
            )

    async def test_concurrent_seed_rows_keep_their_answers_async(self) -> None:
        groups = [_group("default"), _group("other")]
        attack = PromptSendingAttack(
            objective_target=MockPromptTarget(),
            attack_scoring_config=AttackScoringConfig(objective_scorer=QuestionAnswerScorer()),
        )

        batch = await AttackExecutor(max_concurrency=2).execute_attack_from_seed_groups_async(
            attack=attack, seed_groups=groups
        )

        results = batch.get_results()
        assert [result.outcome for result in results] == [AttackOutcome.SUCCESS, AttackOutcome.FAILURE]
        for result, group in zip(results, groups, strict=True):
            assert result.automated_score is not None
            assert result.automated_score.scored_expectation == group.scoring_expectation

    async def test_unmatched_seed_condition_fails_before_send_async(self) -> None:
        target = MockPromptTarget()
        attack = PromptSendingAttack(
            objective_target=target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=QuestionAnswerScorer()),
        )
        group = AttackSeedGroup(seeds=[SeedObjective(value="objective", conditions=(MatchesObjective(),))])
        params = await AttackParameters.from_seed_group_async(seed_group=group)

        with pytest.raises(ValueError, match="condition"):
            await attack.execute_async(objective=params.objective, expectation=params.expectation)

        assert target.prompt_sent == []
