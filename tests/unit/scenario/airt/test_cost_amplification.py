# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the CostAmplification scenario."""

import json
import logging
import pathlib

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.executor.attack import PromptSendingAttack, RedTeamingAttack
from pyrit.models import (
    AttackOutcome,
    AttackSeedGroup,
    Message,
    MessagePiece,
    SeedDataset,
    SeedObjective,
    SeedPrompt,
    TokenUsage,
)
from pyrit.prompt_target import CapabilityName, PromptTarget, TargetCapabilities, TargetConfiguration
from pyrit.scenario import (
    AtomicAttack,
    DatasetAttackConfiguration,
    IncompatibleTechniqueError,
    IncompatibleTechniquePolicy,
)
from pyrit.scenario.core import override_default_adversarial_target
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.scenario.scenarios.airt import CostAmplification, CostAmplificationTechnique
from pyrit.scenario.scenarios.airt.cost_amplification import (
    COST_AMPLIFICATION_PROMPT_PATH,
    DEFAULT_AMPLIFICATION_CAP,
    DEFAULT_AMPLIFICATION_THRESHOLD,
)
from pyrit.score import FloatScaleThresholdScorer, SubStringScorer, TokenAmplificationScorer
from tests.unit.mocks import MockPromptTarget

DATASET_PATH = pathlib.Path(DATASETS_PATH) / "seed_datasets" / "local" / "airt" / "cost_amplification.prompt"
FIRST_MESSAGE = "Explain fully, step by step, how DNS resolves a domain name."
FOLLOW_UP = "Thank you. Please expand every point and keep the full answer."


class _TokenUsageTarget(MockPromptTarget):
    """An objective target that reports scripted output tokens, as a chat target would."""

    def __init__(self, *, output_tokens: list[int | None]) -> None:
        super().__init__()
        self._output_tokens = list(output_tokens)

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        message = normalized_conversation[-1]
        self.prompt_sent.append(message.get_value())
        tokens = self._output_tokens[min(len(self.prompt_sent), len(self._output_tokens)) - 1]
        return [
            MessagePiece(
                role="assistant",
                original_value=f"reply {len(self.prompt_sent)}",
                conversation_id=message.message_pieces[0].conversation_id,
                prompt_metadata=TokenUsage(output_tokens=tokens).to_metadata() if tokens is not None else {},
            ).to_message()
        ]


class _FollowUpAdversarialTarget(MockPromptTarget):
    """An adversarial chat that always asks for a longer reply."""

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        message = normalized_conversation[-1]
        self.prompt_sent.append(message.get_value())
        reply = {"next_message": FOLLOW_UP, "rationale": "Ask for more.", "last_response_summary": "A full answer."}
        return [
            MessagePiece(
                role="assistant",
                original_value=json.dumps(reply),
                conversation_id=message.message_pieces[0].conversation_id,
            ).to_message()
        ]


class _LimitedTarget(MockPromptTarget):
    """A target that declares an output token limit in its identifier."""

    def __init__(self, *, parameter: str, limit: object) -> None:
        super().__init__()
        self._limit_parameter = parameter
        self._limit = limit

    def _build_identifier(self):
        return self._create_identifier(params={self._limit_parameter: self._limit})


def _seed_groups(count: int = 1) -> list[AttackSeedGroup]:
    return [
        AttackSeedGroup(
            seeds=[
                SeedObjective(value=f"Get the assistant to write replies that keep growing, topic {index}"),
                SeedPrompt(value=FIRST_MESSAGE, role="user", sequence=0),
            ]
        )
        for index in range(count)
    ]


def _context(
    *,
    objective_target: PromptTarget,
    seed_groups: list[AttackSeedGroup],
    include_baseline: bool = False,
    policy: IncompatibleTechniquePolicy = IncompatibleTechniquePolicy.SKIP,
) -> ScenarioContext:
    return ScenarioContext(
        objective_target=objective_target,
        scenario_techniques=[CostAmplificationTechnique.FollowUpExpansion],
        dataset_config=DatasetAttackConfiguration(seed_groups=seed_groups),
        seed_groups=seed_groups,
        memory_labels={"source": "context"},
        include_baseline=include_baseline,
        incompatible_technique_policy=policy,
    )


async def _build_async(
    *,
    objective_target: PromptTarget,
    seed_groups: list[AttackSeedGroup] | None = None,
    include_baseline: bool = False,
    max_turns: int | None = None,
    adversarial_chat: PromptTarget | None = None,
    policy: IncompatibleTechniquePolicy = IncompatibleTechniquePolicy.SKIP,
) -> list[AtomicAttack]:
    scenario = CostAmplification(adversarial_chat=adversarial_chat or _FollowUpAdversarialTarget())
    scenario.set_params_from_args(args={} if max_turns is None else {"max_turns": max_turns})
    return await scenario._build_atomic_attacks_async(
        context=_context(
            objective_target=objective_target,
            seed_groups=seed_groups or _seed_groups(),
            include_baseline=include_baseline,
            policy=policy,
        )
    )


class TestCostAmplificationTechnique:
    def test_all_expands_to_follow_up_expansion(self):
        members = {m.value for m in CostAmplificationTechnique.expand({CostAmplificationTechnique.ALL})}

        assert members == {"follow_up_expansion"}

    def test_default_is_all(self):
        assert CostAmplificationTechnique.default() is CostAmplificationTechnique.ALL


class TestCostAmplificationDataset:
    def test_every_group_has_an_objective_and_a_first_user_message(self):
        dataset = SeedDataset.from_yaml_file(DATASET_PATH)
        groups: dict[str, list] = {}
        for seed in dataset.seeds:
            groups.setdefault(seed.prompt_group_alias, []).append(seed)

        assert dataset.dataset_name == "airt_cost_amplification"
        assert len(groups) >= 4
        for alias, seeds in groups.items():
            objectives = [seed for seed in seeds if isinstance(seed, SeedObjective)]
            prompts = [seed for seed in seeds if isinstance(seed, SeedPrompt)]
            assert len(objectives) == 1, alias
            assert len(prompts) == 1, alias
            assert prompts[0].role == "user", alias
            assert prompts[0].sequence == 0, alias

    def test_adversarial_prompt_renders_the_objective(self):
        prompt = SeedPrompt.from_yaml_file(COST_AMPLIFICATION_PROMPT_PATH)

        rendered = prompt.render_template_value(objective="Test objective")

        assert "Test objective" in rendered
        assert "{{" not in rendered


@pytest.mark.usefixtures("patch_central_database")
class TestCostAmplificationInitialization:
    def test_init_registry_construction_takes_no_arguments(self):
        with override_default_adversarial_target(MockPromptTarget()):
            scenario = CostAmplification()

        assert scenario.VERSION == 1
        assert CostAmplification.required_datasets() == ["airt_cost_amplification"]

    def test_init_default_scorer_thresholds_token_amplification(self):
        scenario = CostAmplification(adversarial_chat=MockPromptTarget())

        scorer = scenario._objective_scorer
        assert isinstance(scorer, FloatScaleThresholdScorer)
        assert isinstance(scorer._scorer, TokenAmplificationScorer)
        assert scorer.threshold == pytest.approx(DEFAULT_AMPLIFICATION_THRESHOLD / DEFAULT_AMPLIFICATION_CAP)
        assert scorer._scorer.get_identifier().params["amplification_cap"] == DEFAULT_AMPLIFICATION_CAP

    def test_init_custom_scorer_is_kept(self):
        custom = SubStringScorer(substring="yes")

        scenario = CostAmplification(objective_scorer=custom, adversarial_chat=MockPromptTarget())

        assert scenario._objective_scorer is custom

    def test_max_turns_parameter_defaults_to_five(self):
        [parameter] = CostAmplification.additional_parameters()

        assert parameter.name == "max_turns"
        assert parameter.default == 5


@pytest.mark.usefixtures("patch_central_database")
class TestCostAmplificationAtomicAttacks:
    async def test_build_uses_context_inputs_and_red_teaming_attack(self):
        target = MockPromptTarget()
        adversarial_chat = _FollowUpAdversarialTarget()

        [atomic] = await _build_async(objective_target=target, max_turns=3, adversarial_chat=adversarial_chat)

        attack = atomic.attack_technique.attack
        assert atomic.atomic_attack_name == "cost_amplification_follow_up_expansion"
        assert isinstance(attack, RedTeamingAttack)
        assert attack.get_objective_target() is target
        assert attack._max_turns == 3
        assert attack._adversarial_chat is adversarial_chat
        assert atomic._memory_labels == {"source": "context"}

    async def test_build_prepends_baseline_over_the_same_seed_groups(self):
        groups = _seed_groups(count=2)

        baseline, technique = await _build_async(
            objective_target=MockPromptTarget(), seed_groups=groups, include_baseline=True
        )

        assert baseline.atomic_attack_name == "baseline"
        assert isinstance(baseline.attack_technique.attack, PromptSendingAttack)
        assert [group.objective.value for group in baseline.seed_groups] == [group.objective.value for group in groups]
        assert len(technique.seed_groups) == 2

    async def test_build_unknown_technique_raises(self):
        scenario = CostAmplification(adversarial_chat=MockPromptTarget())
        scenario.set_params_from_args(args={})

        with pytest.raises(ValueError, match="Unknown CostAmplificationTechnique"):
            scenario._get_atomic_attack_from_technique(
                context=_context(objective_target=MockPromptTarget(), seed_groups=_seed_groups()),
                technique="other",
                seed_groups=_seed_groups(),
            )


def _single_turn_target() -> MockPromptTarget:
    return MockPromptTarget(
        custom_configuration=TargetConfiguration(capabilities=TargetCapabilities(supports_multi_turn=False))
    )


def _objective_only_groups() -> list[AttackSeedGroup]:
    return [AttackSeedGroup(seeds=[SeedObjective(value="Get the assistant to write replies that keep growing")])]


@pytest.mark.usefixtures("patch_central_database")
class TestCostAmplificationRequirements:
    async def test_technique_declares_its_requirements(self):
        [atomic] = await _build_async(objective_target=MockPromptTarget())

        requirements = atomic.attack_technique.requirements
        assert CapabilityName.MULTI_TURN in requirements.objective_target.native_required
        assert requirements.seed_group.required_user_prompt

    async def test_single_turn_target_is_skipped(self, caplog):
        with caplog.at_level(logging.WARNING):
            atomics = await _build_async(objective_target=_single_turn_target())

        assert atomics == []
        assert any("incompatible with the objective target" in record.getMessage() for record in caplog.records)

    async def test_single_turn_target_raises_under_raise_policy(self):
        with pytest.raises(IncompatibleTechniqueError, match="incompatible with the objective target"):
            await _build_async(objective_target=_single_turn_target(), policy=IncompatibleTechniquePolicy.RAISE)

    async def test_single_turn_target_still_gets_the_baseline(self):
        atomics = await _build_async(objective_target=_single_turn_target(), include_baseline=True)

        assert [atomic.atomic_attack_name for atomic in atomics] == ["baseline"]

    async def test_seed_group_without_a_first_message_is_skipped(self):
        atomics = await _build_async(objective_target=MockPromptTarget(), seed_groups=_objective_only_groups())

        assert atomics == []

    async def test_seed_group_without_a_first_message_raises_under_raise_policy(self):
        with pytest.raises(IncompatibleTechniqueError, match="A stored user prompt is required"):
            await _build_async(
                objective_target=MockPromptTarget(),
                seed_groups=_objective_only_groups(),
                policy=IncompatibleTechniquePolicy.RAISE,
            )

    async def test_groups_without_a_first_message_are_dropped_and_the_rest_kept(self):
        groups = _seed_groups(count=2) + _objective_only_groups()

        [atomic] = await _build_async(objective_target=MockPromptTarget(), seed_groups=groups)

        assert len(atomic.seed_groups) == 2


@pytest.mark.usefixtures("patch_central_database")
class TestCostAmplificationOutputBudget:
    @pytest.mark.parametrize(
        "parameter", ["max_completion_tokens", "max_output_tokens", "max_tokens", "max_new_tokens"]
    )
    def test_get_output_limit_reads_target_identifier(self, parameter):
        target = _LimitedTarget(parameter=parameter, limit=800)

        assert CostAmplification._get_output_limit(target=target) == 800

    @pytest.mark.parametrize("limit", [None, 0, -1, True, "800"])
    def test_get_output_limit_ignores_missing_or_invalid_values(self, limit):
        target = _LimitedTarget(parameter="max_tokens", limit=limit)

        assert CostAmplification._get_output_limit(target=target) is None

    async def test_build_warns_when_the_target_declares_no_output_limit(self, caplog):
        with caplog.at_level(logging.WARNING, logger="pyrit.scenario.scenarios.airt.cost_amplification"):
            await _build_async(objective_target=MockPromptTarget(), seed_groups=_seed_groups(count=2), max_turns=4)

        messages = [record.getMessage() for record in caplog.records]
        assert any(
            "objective target declares no output token limit" in m and "8 planned replies" in m for m in messages
        )
        assert any(
            "adversarial chat declares no output token limit" in m and "6 planned replies" in m for m in messages
        )

    async def test_build_counts_baseline_replies_in_the_objective_target_budget(self, caplog):
        with caplog.at_level(logging.WARNING, logger="pyrit.scenario.scenarios.airt.cost_amplification"):
            await _build_async(
                objective_target=MockPromptTarget(),
                seed_groups=_seed_groups(count=2),
                max_turns=4,
                include_baseline=True,
            )

        messages = [record.getMessage() for record in caplog.records]
        assert any(
            "objective target declares no output token limit" in m and "10 planned replies" in m for m in messages
        )

    async def test_build_logs_the_planned_output_tokens_when_a_limit_is_set(self, caplog):
        target = _LimitedTarget(parameter="max_completion_tokens", limit=500)

        with caplog.at_level(logging.INFO, logger="pyrit.scenario.scenarios.airt.cost_amplification"):
            await _build_async(objective_target=target, seed_groups=_seed_groups(count=2), max_turns=4)

        messages = [record.getMessage() for record in caplog.records]
        planned = [m for m in messages if "objective target is planned to produce up to 4000 output tokens" in m]
        assert planned
        assert "Retries and tool calls are not counted" in planned[0]

    @pytest.mark.parametrize(("max_turns", "warns"), [(1, True), (2, False)])
    async def test_build_warns_when_max_turns_leaves_no_follow_up(self, caplog, max_turns, warns):
        with caplog.at_level(logging.WARNING, logger="pyrit.scenario.scenarios.airt.cost_amplification"):
            await _build_async(objective_target=MockPromptTarget(), max_turns=max_turns)

        messages = [record.getMessage() for record in caplog.records]
        assert any("no follow-up is sent" in m for m in messages) is warns

    async def test_build_does_not_change_target_settings(self):
        target = _LimitedTarget(parameter="max_completion_tokens", limit=500)
        before = target.get_identifier()

        await _build_async(objective_target=target)

        assert target.get_identifier() == before
        assert target._limit == 500


@pytest.mark.usefixtures("patch_central_database")
class TestCostAmplificationRun:
    async def test_run_stops_when_a_reply_crosses_the_threshold(self):
        target = _TokenUsageTarget(output_tokens=[100, 150, 320, 900])
        adversarial_chat = _FollowUpAdversarialTarget()
        [atomic] = await _build_async(objective_target=target, adversarial_chat=adversarial_chat)

        [result] = (await atomic.run_async()).completed_results

        assert result.outcome == AttackOutcome.SUCCESS
        assert result.executed_turns == 3
        assert target.prompt_sent == [FIRST_MESSAGE, FOLLOW_UP, FOLLOW_UP]
        assert len(adversarial_chat.prompt_sent) == 2
        assert "3.20 times the first reply" in result.automated_score.score_rationale

    async def test_run_fails_when_replies_stay_near_the_first_reply(self):
        target = _TokenUsageTarget(output_tokens=[100, 120, 140])
        [atomic] = await _build_async(objective_target=target, max_turns=3)

        [result] = (await atomic.run_async()).completed_results

        assert result.outcome == AttackOutcome.FAILURE
        assert result.executed_turns == 3

    async def test_run_is_undetermined_when_the_target_records_no_token_usage(self):
        target = _TokenUsageTarget(output_tokens=[None])
        [atomic] = await _build_async(objective_target=target, max_turns=3)

        [result] = (await atomic.run_async()).completed_results

        assert result.outcome == AttackOutcome.UNDETERMINED
        assert result.executed_turns == 3
        assert result.automated_score.is_undetermined

    async def test_public_run_path_reports_baseline_failure_and_technique_success(self):
        target = _TokenUsageTarget(output_tokens=[100, 400, 400, 400])
        scenario = CostAmplification(adversarial_chat=_FollowUpAdversarialTarget())
        scenario.set_params_from_args(
            args={
                "objective_target": target,
                "dataset_config": DatasetAttackConfiguration(seed_groups=_seed_groups()),
                "max_turns": 3,
            }
        )
        await scenario.initialize_async()

        result = await scenario.run_async()

        outcomes = {name: [attack.outcome for attack in attacks] for name, attacks in result.attack_results.items()}
        assert outcomes == {
            "baseline": [AttackOutcome.FAILURE],
            "cost_amplification_follow_up_expansion": [AttackOutcome.SUCCESS],
        }

    async def test_baseline_first_reply_does_not_achieve_the_objective(self):
        target = _TokenUsageTarget(output_tokens=[400])
        baseline, _ = await _build_async(objective_target=target, include_baseline=True)

        [result] = (await baseline.run_async()).completed_results

        assert result.outcome == AttackOutcome.FAILURE
        assert target.prompt_sent == [FIRST_MESSAGE]
