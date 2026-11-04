# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Explicit execution roles must resolve before datasets or model requests."""

from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.backend.services.scenario_run_service import ScenarioRunService
from pyrit.executor.attack import PromptSendingAttack
from pyrit.memory import CentralMemory
from pyrit.models.catalog import ScenarioRunSizeEstimateRequest
from pyrit.models.scenario_readiness import ScenarioConfigurationError
from pyrit.prompt_target import TargetCapabilities
from pyrit.registry import AttackTechniqueRegistry, ScenarioRegistry, ScorerRegistry, TargetRegistry
from pyrit.scenario.core import AttackTechniqueFactory, override_default_adversarial_target
from pyrit.scenario.scenarios.technique.execute import Execute
from pyrit.score import SubStringScorer
from pyrit.setup.initializers.scorers import ScorerInitializerTags
from pyrit.setup.initializers.techniques import build_technique_factories


@pytest.fixture(autouse=True)
def isolated_roles(monkeypatch: pytest.MonkeyPatch):
    """Keep these lifecycle tests independent of all ambient helper configuration."""
    for registry in (TargetRegistry, ScorerRegistry, AttackTechniqueRegistry, ScenarioRegistry):
        registry.reset_registry_singleton()
    for prefix in ("OPENAI_CHAT", "OBJECTIVE_SCORER_CHAT", "ADVERSARIAL_CHAT"):
        for field in ("MODEL", "ENDPOINT", "KEY"):
            monkeypatch.delenv(f"{prefix}_{field}", raising=False)
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(
        [
            *build_technique_factories(),
            AttackTechniqueFactory(name="prompt_sending", attack_class=PromptSendingAttack),
        ]
    )
    yield
    for registry in (TargetRegistry, ScorerRegistry, AttackTechniqueRegistry, ScenarioRegistry):
        registry.reset_registry_singleton()


def configured_execute(*, technique: str = "prompt_sending", deterministic: bool = True) -> Execute:
    """Build a real one-objective configuration, not a stand-in scorer or target."""
    scenario = Execute(objective_scorer=SubStringScorer(substring="default") if deterministic else None)
    scenario.set_params_from_args(
        args={
            "objective": "Return a response",
            "objective_target": MockPromptTarget(),
            "scenario_techniques": [scenario._technique_class(technique)],
        }
    )
    scenario._resolve_runtime_configuration(require_objective_target=False)
    return scenario


@pytest.mark.usefixtures("patch_central_database")
class TestScenarioReadiness:
    def test_catalog_metadata_and_estimates_construct_no_provider(self) -> None:
        with patch("pyrit.prompt_target.OpenAIChatTarget", side_effect=AssertionError("Implicit provider")):
            metadata = ScenarioRegistry.get_registry_singleton().get_all_registered_class_metadata()
        assert metadata
        assert all(item.class_name for item in metadata)

    def test_only_selected_generation_technique_requires_helper(self) -> None:
        plain = configured_execute()
        assert plain.get_readiness().ready
        generated = configured_execute(technique="red_teaming")
        readiness = generated.get_readiness()
        assert [issue.role for issue in readiness.issues] == ["adversarial_target"]
        assert not generated._objective_target.prompt_sent

    async def test_missing_roles_aggregate_before_generation_or_persistence_async(self) -> None:
        scenario = Execute()
        scenario.set_params_from_args(args={"objective": "Return a response"})
        with patch.object(scenario._dataset_config, "prepare_async", new_callable=AsyncMock) as prepare:
            with pytest.raises(ScenarioConfigurationError) as error:
                await scenario.initialize_async()
        assert {issue.role for issue in error.value.issues} == {
            "objective_target",
            "scoring_target",
            "adversarial_target",
        }
        prepare.assert_not_awaited()
        assert await CentralMemory.get_memory_instance().get_scenario_results_async() == []

    async def test_non_openai_target_and_deterministic_scorer_execute_async(self) -> None:
        scenario = configured_execute()
        with patch("pyrit.prompt_target.OpenAIChatTarget", side_effect=AssertionError("Implicit provider")):
            await scenario.initialize_async()
            await scenario.run_async()
        assert scenario._objective_target.prompt_sent
        results = await CentralMemory.get_memory_instance().get_scenario_results_async()
        assert len(results) == 1
        assert results[0].scenario_identifier.objective_target.class_name == "MockPromptTarget"

    def test_registered_deterministic_default_needs_no_scoring_target(self) -> None:
        scorer = SubStringScorer(substring="default")
        ScorerRegistry.get_registry_singleton().instances.register(
            scorer,
            name="deterministic",
            tags=[ScorerInitializerTags.DEFAULT_OBJECTIVE_SCORER],
        )
        scenario = configured_execute(deterministic=False)
        assert scenario.get_readiness().ready
        assert scenario._objective_scorer is scorer
        assert scenario._resolved_scoring_target is None

    def test_multiple_scorer_defaults_require_explicit_selection(self) -> None:
        for name in ("first", "second"):
            ScorerRegistry.get_registry_singleton().instances.register(
                SubStringScorer(substring=name),
                name=name,
                tags=[ScorerInitializerTags.DEFAULT_OBJECTIVE_SCORER],
            )
        readiness = configured_execute(deterministic=False).get_readiness()
        assert any(issue.role == "objective_scorer" and "Multiple" in issue.reason for issue in readiness.issues)
        assert configured_execute().get_readiness().ready

    def test_objective_is_not_implicitly_reused_but_deliberate_role_reuse_works(self) -> None:
        scenario = configured_execute(technique="red_teaming", deterministic=False)
        assert {issue.role for issue in scenario.get_readiness().issues} == {"scoring_target", "adversarial_target"}
        target = scenario._objective_target
        scenario.params["scoring_target"] = target
        with override_default_adversarial_target(target):
            readiness = scenario.get_readiness()
        assert readiness.ready
        assert scenario._resolved_adversarial_target is target
        assert scenario._resolved_scoring_target is target

    def test_generation_binding_is_frozen_after_readiness(self) -> None:
        scenario = configured_execute(technique="red_teaming")
        selected, replacement = MockPromptTarget(id="selected"), MockPromptTarget(id="replacement")
        with override_default_adversarial_target(selected):
            assert scenario.get_readiness().ready
        with override_default_adversarial_target(replacement):
            assert scenario.get_readiness().ready
        assert scenario._resolved_adversarial_target is selected

    def test_explicit_reconfiguration_rebinds_lazy_scoring(self) -> None:
        scenario = configured_execute(deterministic=False)
        first, second = MockPromptTarget(id="first"), MockPromptTarget(id="second")
        for selected in (first, second):
            scenario.set_params_from_args(
                args={
                    **scenario.params,
                    "scoring_target": selected,
                }
            )
            assert scenario.get_readiness().ready
            assert scenario._resolved_scoring_target is selected

    async def test_python_resume_rejects_changed_scorer_before_dataset_work_async(self) -> None:
        scenario = configured_execute()
        await scenario.initialize_async()
        resumed = Execute(
            scenario_result_id=scenario._scenario_result_id,
            objective_scorer=SubStringScorer(substring="changed"),
        )
        resumed.set_params_from_args(args=scenario.params)
        with patch.object(resumed, "_resolve_seed_groups_by_dataset_async", new_callable=AsyncMock) as resolve:
            with pytest.raises(ValueError, match="saved scenario dependencies changed"):
                await resumed.initialize_async()
        resolve.assert_not_awaited()

    def test_objective_capabilities_match_selected_factory(self) -> None:
        scenario = configured_execute(technique="crescendo")
        scenario._objective_target.apply_capabilities(capabilities=TargetCapabilities(supports_multi_turn=False))
        with override_default_adversarial_target(MockPromptTarget()):
            readiness = scenario.get_readiness()
        assert any(issue.role == "objective_target" for issue in readiness.issues)

    def test_preview_aggregates_stale_named_roles(self) -> None:
        request = ScenarioRunSizeEstimateRequest(
            target_name="missing-objective",
            scoring_target_name="missing-scorer",
            adversarial_target_name="missing-generator",
            scenario_params={"objective": "Return a response"},
        )
        readiness = ScenarioConfigurationResolver.resolve_readiness(scenario_name="technique.execute", request=request)
        assert {issue.role for issue in readiness.issues} == {
            "objective_target",
            "scoring_target",
            "adversarial_target",
        }
        assert all(issue.remediation for issue in readiness.issues)

    def test_suite_role_drift_rejected_before_initialization(self) -> None:
        scenario = configured_execute()
        with pytest.raises(ValueError, match="changed after preflight"):
            ScenarioRunService._resolved_role_metadata(scenario, expected_references={"objective_target_hash": "stale"})
