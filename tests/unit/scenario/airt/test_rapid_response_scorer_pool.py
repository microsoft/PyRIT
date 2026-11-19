# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""RapidResponse with an objective scorer pool: selection, readiness, identity, run plan, and resume."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.models import (
    AttackSeedGroup,
    ComponentIdentifier,
    ScenarioEvaluationIdentifier,
    ScenarioIdentifier,
    ScenarioRunPlan,
    ScenarioRunPlanGroupKind,
    SeedObjective,
    TargetIdentifier,
)
from pyrit.prompt_target import PromptTarget
from pyrit.registry import AttackTechniqueRegistry, TargetRegistry
from pyrit.scenario import CompoundDatasetAttackConfiguration, DatasetAttackConfiguration, DatasetSource
from pyrit.scenario.core import DatasetConstraintError
from pyrit.scenario.core.atomic_attack import compute_atomic_group_id
from pyrit.scenario.scenarios.airt import Cyber, Jailbreak, Leakage, RapidResponse
from pyrit.score import CompatibleScorers, ScorerCandidate, ScorerRequirements, SubStringScorer
from pyrit.setup.initializers.techniques import build_technique_factories

HATE = "harm.hate"
VIOLENCE = "harm.violence"
FIXTURES = ["patch_central_database", "mock_dataset_preparation"]


def _target_id(name: str) -> TargetIdentifier:
    return TargetIdentifier(class_name=name, class_module="test")


@pytest.fixture(autouse=True)
def technique_registry():
    from pyrit.scenario.scenarios.airt.cyber import _build_cyber_technique
    from pyrit.scenario.scenarios.airt.leakage import _build_leakage_technique
    from pyrit.scenario.scenarios.airt.rapid_response import _build_rapid_response_technique

    builders = (_build_rapid_response_technique, _build_cyber_technique, _build_leakage_technique)
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    for builder in builders:
        builder.cache_clear()
    adversarial = MagicMock(spec=PromptTarget)
    adversarial.capabilities.includes.return_value = True
    adversarial.get_identifier.return_value = _target_id("MockAdversarialTarget")
    TargetRegistry.get_registry_singleton().instances.register(adversarial, name="adversarial_chat")
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(build_technique_factories())
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    for builder in builders:
        builder.cache_clear()


@pytest.fixture
def objective_target():
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = _target_id("MockObjectiveTarget")
    return target


@pytest.fixture
def judges():
    return SubStringScorer(substring="hate"), SubStringScorer(substring="violence")


def _pool(hate: SubStringScorer, violence: SubStringScorer) -> CompatibleScorers:
    return CompatibleScorers(
        candidates=[
            ScorerCandidate(scorer=hate, evaluation=HATE),
            ScorerCandidate(scorer=violence, evaluation=VIOLENCE),
        ]
    )


def _dataset_config(*, violence_evaluation: str = VIOLENCE) -> DatasetAttackConfiguration:
    return DatasetAttackConfiguration(
        sources=[
            DatasetSource(name="airt_hate", scorer_requirements=ScorerRequirements(evaluation=HATE)),
            DatasetSource(name="airt_violence", scorer_requirements=ScorerRequirements(evaluation=violence_evaluation)),
        ]
    )


GROUPS = {
    "airt_hate": [AttackSeedGroup(seeds=[SeedObjective(value=f"hate objective {i}")]) for i in (1, 2)],
    "airt_violence": [AttackSeedGroup(seeds=[SeedObjective(value="violence objective")])],
}


async def _initialize(scenario: RapidResponse, *, target, dataset_config, groups=None) -> RapidResponse:
    from pyrit.scenario.scenarios.airt.rapid_response import _build_rapid_response_technique

    with patch.object(
        DatasetAttackConfiguration,
        "get_attack_groups_by_dataset_async",
        new_callable=AsyncMock,
        return_value=groups or GROUPS,
    ):
        scenario.set_params_from_args(
            args={
                "objective_target": target,
                "dataset_config": dataset_config,
                "include_baseline": True,
                "scenario_techniques": [_build_rapid_response_technique()("flip")],
            }
        )
        await scenario.initialize_async()
    return scenario


def test_scenarios_that_do_not_select_from_pools_reject_them(judges):
    with pytest.raises(TypeError, match="Jailbreak does not accept a CompatibleScorers pool"):
        Jailbreak(objective_scorer=_pool(*judges))


@pytest.mark.usefixtures(*FIXTURES)
async def test_scenarios_that_do_not_check_dataset_scorer_requirements_reject_them(objective_target, judges):
    scenario = Jailbreak(objective_scorer=judges[0])
    scenario.set_params_from_args(args={"objective_target": objective_target, "dataset_config": _dataset_config()})
    with pytest.raises(ValueError, match="Jailbreak does not check DatasetSource.scorer_requirements.*airt_hate"):
        await scenario.initialize_async()


@pytest.mark.usefixtures(*FIXTURES)
@pytest.mark.parametrize("scenario_class", [RapidResponse, Cyber, Leakage])
async def test_opted_in_scenarios_judge_each_dataset_with_its_bound_scorer(scenario_class, objective_target, judges):
    hate, violence = judges
    scenario = scenario_class(objective_scorer=_pool(hate, violence))
    with patch.object(
        DatasetAttackConfiguration, "get_attack_groups_by_dataset_async", new_callable=AsyncMock, return_value=GROUPS
    ):
        scenario.set_params_from_args(
            args={"objective_target": objective_target, "dataset_config": _dataset_config(), "include_baseline": True}
        )
        await scenario.initialize_async()

    expected = {"airt_hate": hate, "airt_violence": violence}
    seen = set()
    for attack in scenario._atomic_attacks:
        dataset = next(name for name in expected if name in attack.atomic_attack_name)
        assert attack._objective_scorer is expected[dataset], attack.atomic_attack_name
        assert attack.objective_scorer_binding is not None
        seen.add((dataset, attack.group_kind is ScenarioRunPlanGroupKind.BASELINE))
    assert seen == {(dataset, is_baseline) for dataset in expected for is_baseline in (True, False)}


class TestDatasetScorerRequirements:
    def test_a_compound_merges_its_childrens_requirements(self) -> None:
        compound = CompoundDatasetAttackConfiguration(
            configurations=[
                DatasetAttackConfiguration(
                    sources=[DatasetSource(name="airt_hate", scorer_requirements=ScorerRequirements(evaluation=HATE))]
                ),
                _dataset_config(),
            ]
        )
        assert compound.get_scorer_requirements_by_dataset() == {
            "airt_hate": ScorerRequirements(evaluation=HATE),
            "airt_violence": ScorerRequirements(evaluation=VIOLENCE),
        }

    def test_compound_children_that_disagree_on_a_dataset_raise(self) -> None:
        compound = CompoundDatasetAttackConfiguration(
            configurations=[_dataset_config(), _dataset_config(violence_evaluation="harm.other")]
        )
        with pytest.raises(DatasetConstraintError, match="Dataset 'airt_violence' declares different scorer"):
            compound.get_scorer_requirements_by_dataset()

    def test_a_source_rejects_other_requirement_types(self) -> None:
        with pytest.raises(DatasetConstraintError, match="'scorer_requirements' must be ScorerRequirements"):
            DatasetSource(name="airt_hate", scorer_requirements=HATE)  # type: ignore[arg-type]


@pytest.mark.usefixtures(*FIXTURES)
class TestRapidResponseScorerPool:
    async def test_each_harm_dataset_is_judged_by_its_bound_scorer(self, objective_target, judges):
        hate, violence = judges
        scenario = await _initialize(
            RapidResponse(objective_scorer=_pool(hate, violence)),
            target=objective_target,
            dataset_config=_dataset_config(),
        )

        scorers = {attack.atomic_attack_name: attack._objective_scorer for attack in scenario._atomic_attacks}
        assert scorers == {
            "baseline_airt_hate": hate,
            "baseline_airt_violence": violence,
            "flip_airt_hate": hate,
            "flip_airt_violence": violence,
        }

    async def test_run_plan_records_each_selection(self, objective_target, judges):
        scenario = await _initialize(
            RapidResponse(objective_scorer=_pool(*judges)),
            target=objective_target,
            dataset_config=_dataset_config(),
        )

        plan = scenario._build_run_plan()
        bindings = {group.atomic_attack_name: group.objective_scorer_binding for group in plan.atomic_groups}
        assert {name: binding.evaluation for name, binding in bindings.items()} == {
            "baseline_airt_hate": HATE,
            "baseline_airt_violence": VIOLENCE,
            "flip_airt_hate": HATE,
            "flip_airt_violence": VIOLENCE,
        }
        baseline = next(g for g in plan.atomic_groups if g.atomic_attack_name == "baseline_airt_hate")
        assert baseline.kind is ScenarioRunPlanGroupKind.BASELINE
        assert set(bindings["flip_airt_hate"].model_dump()) == {"candidate_index", "evaluation", "scorer_hash"}
        dumped = plan.model_dump(mode="json", exclude_none=True)
        assert all("objective_scorer_binding" in group for group in dumped["atomic_groups"])

    async def test_single_scorer_run_plan_keeps_its_shape_and_ids(self, objective_target, judges):
        hate, _ = judges
        scenario = await _initialize(
            RapidResponse(objective_scorer=hate),
            target=objective_target,
            dataset_config=DatasetAttackConfiguration(sources=[DatasetSource(name="airt_hate")]),
            groups={"airt_hate": GROUPS["airt_hate"]},
        )

        plan = scenario._build_run_plan().model_dump(mode="json", exclude_none=True)
        assert all("objective_scorer_binding" not in group for group in plan["atomic_groups"])
        from pyrit.models import config_hash

        attack = scenario._atomic_attacks[-1]
        assert scenario._get_atomic_group_id(atomic_attack=attack) == config_hash(
            {
                "atomic_attack_name": attack.atomic_attack_name,
                "technique_eval_hash": str(attack.technique_eval_hash),
            }
        )
        assert attack.logical_group_id == scenario._get_atomic_group_id(atomic_attack=attack)

    async def test_workers_track_pool_groups_under_their_plan_ids(self, objective_target, judges):
        scenario = await _initialize(
            RapidResponse(objective_scorer=_pool(*judges)),
            target=objective_target,
            dataset_config=_dataset_config(),
        )
        from pyrit.models import config_hash

        planned = {group.atomic_attack_name: group.id for group in scenario._build_run_plan().atomic_groups}
        for attack in scenario._atomic_attacks:
            # Workers publish logical_group_id as the active group, and progress matches it to the plan.
            assert attack.logical_group_id == planned[attack.atomic_attack_name]
            assert attack.logical_group_id != config_hash(
                {
                    "atomic_attack_name": attack.atomic_attack_name,
                    "technique_eval_hash": str(attack.technique_eval_hash),
                }
            )

    async def test_readiness_lists_every_candidate(self, objective_target, judges):
        hate, violence = judges
        scenario = RapidResponse(objective_scorer=_pool(hate, violence))
        scenario.set_params_from_args(args={"objective_target": objective_target, "dataset_config": _dataset_config()})

        readiness = scenario.get_readiness()

        candidates = {
            binding.role: (binding.component, binding.identifier.hash)
            for binding in readiness.bindings
            if binding.role.startswith("objective_scorer")
        }
        assert candidates == {
            "objective_scorer_candidate:0": (HATE, hate.get_identifier().hash),
            "objective_scorer_candidate:1": (VIOLENCE, violence.get_identifier().hash),
        }

    async def test_identity_records_the_pool_but_not_dataset_requirements(self, objective_target, judges):
        hate, violence = judges

        async def identity(pool: CompatibleScorers, config: DatasetAttackConfiguration):
            scenario = await _initialize(
                RapidResponse(objective_scorer=pool), target=objective_target, dataset_config=config
            )
            return scenario._build_scenario_identifier()

        base = await identity(_pool(hate, violence), _dataset_config())
        assert base.objective_scorer is None
        pool_params = base.get_child("objective_scorer_pool").params
        assert pool_params["evaluations"] == [HATE, VIOLENCE]
        assert "requirements" not in pool_params
        same = await identity(_pool(hate, violence), _dataset_config())
        reordered = await identity(
            CompatibleScorers(
                candidates=[
                    ScorerCandidate(scorer=violence, evaluation=VIOLENCE),
                    ScorerCandidate(scorer=hate, evaluation=HATE),
                ]
            ),
            _dataset_config(),
        )
        assert base.hash == same.hash
        assert base.hash != reordered.hash
        assert ScenarioEvaluationIdentifier(base).eval_hash != ScenarioEvaluationIdentifier(reordered).eval_hash

        # A requirement that changes a selection changes the plan's group ids instead.
        both_hate = DatasetAttackConfiguration(
            sources=[
                DatasetSource(name="airt_hate", scorer_requirements=ScorerRequirements(evaluation=HATE)),
                DatasetSource(name="airt_violence", scorer_requirements=ScorerRequirements(evaluation=HATE)),
            ]
        )
        assert (await identity(_pool(hate, violence), both_hate)).hash == base.hash

    async def test_the_pool_identity_round_trips_through_storage(self, objective_target, judges):
        scenario = await _initialize(
            RapidResponse(objective_scorer=_pool(*judges)), target=objective_target, dataset_config=_dataset_config()
        )
        identity = scenario._build_scenario_identifier()

        stored = ScenarioIdentifier.from_component_identifier(ComponentIdentifier.model_validate(identity.model_dump()))

        assert stored.objective_scorer is None
        assert stored.get_child("objective_scorer_pool").hash == identity.get_child("objective_scorer_pool").hash
        assert ScenarioEvaluationIdentifier(stored).eval_hash == ScenarioEvaluationIdentifier(identity).eval_hash

    async def _saved_plan(self, objective_target, judges) -> tuple[RapidResponse, ScenarioRunPlan]:
        scenario = await _initialize(
            RapidResponse(objective_scorer=_pool(*judges)),
            target=objective_target,
            dataset_config=_dataset_config(),
        )
        # Resume reads the plan back from stored JSON.
        return scenario, ScenarioRunPlan.model_validate_json(scenario._build_run_plan().model_dump_json())

    @staticmethod
    def _with_binding(plan: ScenarioRunPlan, *, update: dict) -> ScenarioRunPlan:
        planned = plan.atomic_groups[-1]
        binding = planned.objective_scorer_binding.model_copy(update=update)
        group_update: dict = {"objective_scorer_binding": binding}
        group_update["id"] = compute_atomic_group_id(
            atomic_attack_name=planned.atomic_attack_name,
            technique_eval_hash=planned.technique_eval_hash,
            objective_scorer_binding=binding,
        )
        groups = [*plan.atomic_groups[:-1], planned.model_copy(update=group_update)]
        return plan.model_copy(update={"atomic_groups": groups})

    async def test_resume_replays_an_unchanged_saved_plan(self, objective_target, judges):
        scenario, stored = await self._saved_plan(objective_target, judges)

        scenario._apply_persisted_run_plan(stored_plan=stored)

        assert [a.atomic_attack_name for a in scenario._atomic_attacks] == [
            group.atomic_attack_name for group in stored.atomic_groups
        ]

    async def test_resume_refuses_a_group_saved_under_another_selection(self, objective_target, judges):
        scenario, stored = await self._saved_plan(objective_target, judges)

        # A different selection is a different unit of work, so the saved group no longer exists.
        reselected = self._with_binding(stored, update={"candidate_index": 0})
        with pytest.raises(ValueError, match="1 planned atomic group\\(s\\) are no longer reconstructable"):
            scenario._apply_persisted_run_plan(stored_plan=reselected)

    async def test_a_mistyped_dataset_evaluation_fails_at_configuration(self, objective_target, judges):
        with pytest.raises(
            ValueError,
            match=r"'harm\.violance' \(required by dataset 'airt_violence'\)\. "
            r"Evaluations in the pool: 'harm\.hate', 'harm\.violence'",
        ):
            await _initialize(
                RapidResponse(objective_scorer=_pool(*judges)),
                target=objective_target,
                dataset_config=_dataset_config(violence_evaluation="harm.violance"),
            )

    async def test_a_required_evaluation_with_a_single_scorer_fails_at_configuration(self, objective_target, judges):
        with pytest.raises(ValueError, match="single objective scorer is not bound to any evaluation.*'harm.hate'"):
            await _initialize(
                RapidResponse(objective_scorer=judges[0]), target=objective_target, dataset_config=_dataset_config()
            )

    async def test_datasets_without_an_evaluation_need_an_unbound_candidate(self, objective_target, judges):
        undeclared = DatasetAttackConfiguration(
            sources=[DatasetSource(name="airt_hate"), DatasetSource(name="airt_violence")]
        )
        with pytest.raises(ValueError, match="these datasets require none: 'airt_hate', 'airt_violence'"):
            await _initialize(
                RapidResponse(objective_scorer=_pool(*judges)), target=objective_target, dataset_config=undeclared
            )

        general = SubStringScorer(substring="general")
        pool = CompatibleScorers(candidates=[*_pool(*judges).candidates, general])
        scenario = await _initialize(
            RapidResponse(objective_scorer=pool), target=objective_target, dataset_config=undeclared
        )
        assert scenario._atomic_attacks
        assert all(attack._objective_scorer is general for attack in scenario._atomic_attacks)

    async def test_a_scenario_evaluation_no_candidate_carries_fails_at_configuration(self, objective_target, judges):
        with (
            patch.object(
                RapidResponse,
                "_get_objective_scorer_requirements",
                return_value=ScorerRequirements(evaluation="harm.self_harm"),
            ),
            pytest.raises(ValueError, match=r"'harm\.self_harm' \(required by the scenario\)"),
        ):
            await _initialize(
                RapidResponse(objective_scorer=_pool(*judges)),
                target=objective_target,
                dataset_config=DatasetAttackConfiguration(sources=[DatasetSource(name="airt_hate")]),
                groups={"airt_hate": GROUPS["airt_hate"]},
            )


def test_dataset_overrides_keep_a_sources_scorer_requirements():
    from pyrit.models import DatasetSourceInput, ScenarioDatasetOptions, ScenarioDatasetRequest
    from pyrit.scenario.core.dataset_overrides import apply_dataset_overrides

    effective = apply_dataset_overrides(
        config=_dataset_config(),
        request=ScenarioDatasetRequest(sources=[DatasetSourceInput(name="airt_hate", max_size=1)]),
        options=ScenarioDatasetOptions(allowed_names=["airt_hate", "airt_violence"]),
    )
    assert effective.get_scorer_requirements_by_dataset() == {"airt_hate": ScorerRequirements(evaluation=HATE)}
