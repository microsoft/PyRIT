# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Explicit execution roles must resolve before datasets or model requests."""

from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.backend.services.scenario_run_service import ScenarioRunConflictError, ScenarioRunService
from pyrit.converter import StringJoinConverter, TranslationConverter
from pyrit.executor.attack import PromptSendingAttack, RedTeamingAttack
from pyrit.memory import CentralMemory
from pyrit.message_normalizer import HistorySquashNormalizer
from pyrit.models import SeedPrompt
from pyrit.models.catalog import RunScenarioRequest, ScenarioRunSizeEstimateRequest
from pyrit.models.scenario_readiness import ScenarioConfigurationError
from pyrit.prompt_target import CapabilityName, TargetCapabilities, TargetConfiguration, TargetRequirements
from pyrit.registry import AttackTechniqueRegistry, ScenarioRegistry, ScorerRegistry, TargetRegistry
from pyrit.scenario.core import AttackTechniqueFactory, override_default_adversarial_target
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.technique_requirements import TechniqueRequirements
from pyrit.scenario.scenarios.airt.jailbreak import Jailbreak
from pyrit.scenario.scenarios.airt.multilingual import Multilingual
from pyrit.scenario.scenarios.benchmark.adversarial import AdversarialBenchmark
from pyrit.scenario.scenarios.garak.doctor import Doctor
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


def named_target(name: str) -> MockPromptTarget:
    target = MockPromptTarget()
    target.set_model_name(model_name=name)
    return target


@pytest.fixture
def simulated_outer_opt_out_factory() -> AttackTechniqueFactory:
    factory = AttackTechniqueFactory.with_simulated_conversation(
        name="simulated_outer_opt_out",
        adversarial_chat_system_prompt=SeedPrompt(value="Generate the simulated conversation.", data_type="text"),
        uses_adversarial=False,
        supports_additional_request_converters=True,
        requirements=TechniqueRequirements(
            objective_target=TargetRequirements(native_required=frozenset({CapabilityName.MULTI_TURN}))
        ),
    )
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories([factory])
    return factory


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

    @pytest.mark.parametrize("same_target", [True, False])
    def test_registered_model_default_respects_explicit_scoring_target(self, same_target: bool) -> None:
        original_target = named_target("original-judge")
        original = configured_execute(deterministic=False)
        original.params["scoring_target"] = original_target
        assert original.get_readiness().ready
        original_scorer = original._objective_scorer
        ScorerRegistry.get_registry_singleton().instances.register(
            original_scorer, name="rubric", tags=[ScorerInitializerTags.DEFAULT_OBJECTIVE_SCORER]
        )
        configured_default = configured_execute(deterministic=False)
        assert configured_default.get_readiness().ready
        assert configured_default._resolved_scoring_target is original_target
        scenario = configured_execute(deterministic=False)
        scenario.params["scoring_target"] = original_target if same_target else named_target("other-judge")
        readiness = scenario.get_readiness()
        assert readiness.ready is same_target
        assert not original_target.prompt_sent
        if same_target:
            assert scenario._objective_scorer is original_scorer
            assert scenario._resolved_scoring_target is original_target
        else:
            assert any("does not use the selected scoring_target" in issue.reason for issue in readiness.issues)

    @pytest.mark.parametrize("technique", ["red_teaming", "crescendo", "crescendo_simulated", "tap"])
    @pytest.mark.parametrize("baked", [False, True])
    def test_selected_consumer_requires_native_system_prompt(self, technique: str, baked: bool) -> None:
        target = MockPromptTarget()
        target.apply_capabilities(
            capabilities=TargetCapabilities(
                supports_multi_turn=True, supports_editable_history=True, supports_system_prompt=False
            )
        )
        registry = AttackTechniqueRegistry.get_registry_singleton()
        if baked:
            factory = registry.get_factories()[technique]
            registry.register_from_factories(
                [
                    AttackTechniqueFactory(
                        name="native_consumer",
                        attack_class=factory.attack_class,
                        adversarial_chat=target,
                        seed_technique=factory.seed_technique,
                    )
                ]
            )
        scenario = configured_execute(technique="native_consumer" if baked else technique)
        with override_default_adversarial_target(target):
            readiness = scenario.get_readiness()
        assert not readiness.ready
        assert any("system_prompt" in issue.reason for issue in readiness.issues)
        assert not target.prompt_sent

    @pytest.mark.parametrize("native_system_prompt", [False, True])
    async def test_simulation_outer_opt_out_checks_internal_consumer_before_work_async(
        self, simulated_outer_opt_out_factory: AttackTechniqueFactory, native_system_prompt: bool
    ) -> None:
        factory = simulated_outer_opt_out_factory
        assert factory.attack_class is PromptSendingAttack
        assert not factory.uses_adversarial
        assert factory.uses_default_adversarial_target
        scenario = configured_execute(technique=factory.name)
        helper = MockPromptTarget()
        helper.apply_capabilities(
            capabilities=TargetCapabilities(
                supports_multi_turn=True, supports_editable_history=True, supports_system_prompt=native_system_prompt
            )
        )
        with override_default_adversarial_target(helper):
            assert factory.resolve_adversarial_chat() is helper
            readiness = scenario.get_readiness()
            assert readiness.ready is native_system_prompt
            if native_system_prompt:
                await scenario.initialize_async()
                assert scenario._scenario_result_id
            else:
                assert {issue.role for issue in readiness.issues} == {"adversarial_target"}
                assert any("system_prompt" in issue.reason for issue in readiness.issues)
                with (
                    patch.object(scenario._dataset_config, "prepare_async", new_callable=AsyncMock) as prepare,
                    patch.object(scenario, "_build_atomic_attacks_async", new_callable=AsyncMock) as build,
                    pytest.raises(ScenarioConfigurationError, match="system_prompt"),
                ):
                    await scenario.initialize_async()
                prepare.assert_not_awaited()
                build.assert_not_awaited()
        assert len(await CentralMemory.get_memory_instance().get_scenario_results_async()) == int(native_system_prompt)
        assert not helper.prompt_sent
        assert not scenario._objective_target.prompt_sent

    async def test_simulation_outer_opt_out_rejects_before_service_initialization_or_scheduling_async(
        self, simulated_outer_opt_out_factory: AttackTechniqueFactory
    ) -> None:
        target, helper = MockPromptTarget(), MockPromptTarget()
        helper.apply_capabilities(
            capabilities=TargetCapabilities(
                supports_multi_turn=True, supports_editable_history=True, supports_system_prompt=False
            )
        )
        targets = TargetRegistry.get_registry_singleton().instances
        targets.register(target, name="objective")
        targets.register(helper, name="generator")
        ScorerRegistry.get_registry_singleton().instances.register(
            SubStringScorer(substring="default"), name="default", tags=[ScorerInitializerTags.DEFAULT_OBJECTIVE_SCORER]
        )
        request = RunScenarioRequest(
            scenario_name="technique.execute",
            target_name="objective",
            adversarial_target_name="generator",
            techniques=[simulated_outer_opt_out_factory.name],
            scenario_params={"objective": "Return a response"},
        )
        service = ScenarioRunService()
        try:
            with (
                patch.object(service, "_run_initializers_async", new_callable=AsyncMock),
                patch.object(Execute, "initialize_async", new_callable=AsyncMock) as initialize,
                patch.object(DatasetAttackConfiguration, "prepare_async", new_callable=AsyncMock) as prepare,
                patch.object(service, "_schedule_prepared_run_async", new_callable=AsyncMock) as schedule,
                pytest.raises(ScenarioConfigurationError, match="system_prompt"),
            ):
                await service.start_run_async(request=request)
            initialize.assert_not_awaited()
            prepare.assert_not_awaited()
            schedule.assert_not_awaited()
            assert await CentralMemory.get_memory_instance().get_scenario_results_async() == []
            assert not target.prompt_sent
            assert not helper.prompt_sent
        finally:
            await service.shutdown_async()

    @pytest.mark.parametrize(
        ("scenario_class", "technique"),
        [(Jailbreak, "prompt_sending"), (Multilingual, "prompt_sending"), (Doctor, "policy_puppetry")],
    )
    def test_scenario_local_factory_policy_matches_readiness(
        self, scenario_class: type[Scenario], technique: str
    ) -> None:
        AttackTechniqueRegistry.get_registry_singleton().register_from_factories(
            [AttackTechniqueFactory(name=technique, attack_class=RedTeamingAttack)]
        )
        scenario = scenario_class(objective_scorer=SubStringScorer(substring="default"))
        scenario.set_params_from_args(
            args={
                "objective_target": MockPromptTarget(),
                "scenario_techniques": [scenario._technique_class(technique)],
            }
        )
        with override_default_adversarial_target(MockPromptTarget()):
            readiness = scenario.get_readiness()
        factory = scenario._get_readiness_factories()[technique]
        assert factory.attack_class is PromptSendingAttack
        assert any(
            binding.role == f"technique:{technique}" and binding.identifier.hash == factory.get_identifier().hash
            for binding in readiness.bindings
        )

    @pytest.mark.parametrize("registered_factory", [True, False])
    async def test_ordered_additive_converters_guard_resume_before_dataset_work_async(
        self, registered_factory: bool
    ) -> None:
        scenario = configured_execute()
        first, second = StringJoinConverter(join_value="-"), StringJoinConverter(join_value=".")
        scenario.params["technique_converters"] = {"prompt_sending": [first, second]}
        await scenario.initialize_async()
        resumed = Execute(
            scenario_result_id=scenario._scenario_result_id, objective_scorer=SubStringScorer(substring="default")
        )
        resumed.set_params_from_args(
            args={**scenario.params, "technique_converters": {"prompt_sending": [second, first]}}
        )
        if not registered_factory:
            # Keep the declared enum while removing the runtime factory, as with scenario-owned builders.
            with patch.object(resumed, "_get_readiness_factories", return_value={}):
                readiness = resumed.get_readiness()
        else:
            readiness = resumed.get_readiness()
        assert [binding.identifier.hash for binding in readiness.bindings if binding.role.startswith("converter:")] == [
            second.get_identifier().hash,
            first.get_identifier().hash,
        ]
        with patch.object(resumed._dataset_config, "prepare_async", new_callable=AsyncMock) as prepare:
            with pytest.raises(ValueError, match="saved scenario dependencies changed"):
                await resumed.initialize_async()
        prepare.assert_not_awaited()

    def test_additive_converter_binding_includes_nested_generation_target(self) -> None:
        identities: list[str] = []
        for name in ("first", "replacement"):
            scenario = configured_execute()
            scenario.params["technique_converters"] = {
                "prompt_sending": [TranslationConverter(converter_target=named_target(name), language="French")]
            }
            readiness = scenario.get_readiness()
            identities.append(
                next(
                    binding.identifier.hash
                    for binding in readiness.bindings
                    if binding.role == "converter:prompt_sending:0"
                )
            )
        assert identities[0] != identities[1]

    @pytest.mark.parametrize("names", [[], ["missing"]])
    async def test_benchmark_owned_targets_reject_before_dataset_work_async(self, names: list[str]) -> None:
        scenario = AdversarialBenchmark(objective_scorer=SubStringScorer(substring="default"))
        scenario.set_params_from_args(
            args={
                "objective_target": MockPromptTarget(),
                "adversarial_targets": names,
                "scenario_techniques": [scenario._technique_class("red_teaming")],
            }
        )
        with patch.object(scenario._dataset_config, "prepare_async", new_callable=AsyncMock) as prepare:
            with pytest.raises(ScenarioConfigurationError) as error:
                await scenario.initialize_async()
        assert {issue.role for issue in error.value.issues} == {"adversarial_targets"}
        prepare.assert_not_awaited()

    def test_benchmark_owns_targets_without_global_adversarial_default(self) -> None:
        registry = TargetRegistry.get_registry_singleton()
        target = MockPromptTarget(id="benchmark-generator")
        registry.instances.register(target, name="generator")
        scenario = AdversarialBenchmark(objective_scorer=SubStringScorer(substring="default"))
        scenario.set_params_from_args(
            args={
                "objective_target": MockPromptTarget(),
                "adversarial_targets": ["generator"],
                "scenario_techniques": [scenario._technique_class("red_teaming")],
            }
        )
        readiness = scenario.get_readiness()
        assert readiness.ready
        assert any(
            binding.role == "adversarial_targets:0:generator"
            and binding.identifier.hash == target.get_identifier().hash
            for binding in readiness.bindings
        )
        assert not any(binding.role == "adversarial_target" for binding in readiness.bindings)
        expected = {f"{binding.role}_hash": binding.identifier.hash for binding in readiness.bindings}
        registry.instances.register(named_target("replacement"), name="generator", replace=True)
        with pytest.raises(ScenarioRunConflictError, match="changed after preflight"):
            ScenarioRunService._resolved_role_metadata(scenario, expected_references=expected)

    @pytest.mark.parametrize("configured_adapter", [False, True])
    def test_translation_readiness_respects_configured_editable_history_adaptation(
        self, configured_adapter: bool
    ) -> None:
        target = MockPromptTarget(
            custom_configuration=TargetConfiguration(
                capabilities=TargetCapabilities(supports_multi_turn=True, supports_editable_history=False),
                normalizer_overrides={CapabilityName.EDITABLE_HISTORY: HistorySquashNormalizer()}
                if configured_adapter
                else None,
            )
        )
        scenario = Multilingual(objective_scorer=SubStringScorer(substring="default"))
        scenario.set_params_from_args(
            args={
                "objective_target": MockPromptTarget(),
                "scenario_techniques": [scenario._technique_class("prompt_sending")],
            }
        )
        with override_default_adversarial_target(target):
            readiness = scenario.get_readiness()
        assert readiness.ready is configured_adapter
        if not configured_adapter:
            assert any("editable_history" in issue.reason for issue in readiness.issues)
        assert not target.prompt_sent

    @pytest.mark.parametrize("technique", ["crescendo", "simulated_outer_opt_out"])
    def test_skipped_attack_does_not_constrain_translation_only_target(
        self, simulated_outer_opt_out_factory: AttackTechniqueFactory, technique: str
    ) -> None:
        scenario = Multilingual(objective_scorer=SubStringScorer(substring="default"))
        scenario.set_params_from_args(args={"objective_target": MockPromptTarget()})
        scenario._resolve_runtime_configuration(require_objective_target=False)
        scenario.params["scenario_techniques"] = [
            scenario._technique_class(name) for name in (technique, "prompt_sending")
        ]
        scenario._objective_target.apply_capabilities(capabilities=TargetCapabilities(supports_multi_turn=False))
        helper = MockPromptTarget(
            custom_configuration=TargetConfiguration(
                capabilities=TargetCapabilities(
                    supports_multi_turn=True, supports_editable_history=False, supports_system_prompt=False
                ),
                normalizer_overrides={CapabilityName.EDITABLE_HISTORY: HistorySquashNormalizer()},
            )
        )
        with override_default_adversarial_target(helper):
            readiness = scenario.get_readiness()
        assert readiness.ready
        assert any(binding.role == "adversarial_target" for binding in readiness.bindings)
        assert not helper.prompt_sent

    def test_benchmark_technique_overrides_are_effective_readiness_bindings(self) -> None:
        TargetRegistry.get_registry_singleton().instances.register(MockPromptTarget(), name="generator")
        scenario = AdversarialBenchmark(objective_scorer=SubStringScorer(substring="default"))
        scenario.set_params_from_args(
            args={
                "objective_target": MockPromptTarget(),
                "adversarial_targets": ["generator"],
                "scenario_techniques": [scenario._technique_class("red_teaming")],
                "technique_args": ["red_teaming.max_turns=2"],
            }
        )
        readiness = scenario.get_readiness()
        assert readiness.ready
        override = scenario._get_technique_factory_overrides()["red_teaming"]
        binding = next(binding for binding in readiness.bindings if binding.role == "technique:red_teaming")
        assert binding.identifier.hash == override.get_identifier().hash
        assert (
            binding.identifier.hash
            != AttackTechniqueRegistry.get_registry_singleton().get_factories()["red_teaming"].get_identifier().hash
        )

    @pytest.mark.parametrize("changed", [False, True])
    async def test_real_suite_child_admission_enforces_nonempty_frozen_bindings_async(self, changed: bool) -> None:
        scorer = SubStringScorer(substring="default")
        ScorerRegistry.get_registry_singleton().instances.register(
            scorer, name="default", tags=[ScorerInitializerTags.DEFAULT_OBJECTIVE_SCORER]
        )
        original = configured_execute()
        expected = {f"{binding.role}_hash": binding.identifier.hash for binding in original.get_readiness().bindings}
        assert {"objective_target_hash", "objective_scorer_hash", "technique:prompt_sending_hash"} <= expected.keys()
        target = named_target("replacement") if changed else original._objective_target
        request = RunScenarioRequest(
            scenario_name="technique.execute", scenario_params={"objective": "Return a response"}
        )
        service = ScenarioRunService()
        original_prepare = DatasetAttackConfiguration.prepare_async
        try:
            with patch.object(
                DatasetAttackConfiguration, "prepare_async", autospec=True, side_effect=original_prepare
            ) as prepare:
                kwargs = {
                    "request": request,
                    "init_kwargs": {"objective_target": target, "scenario_techniques": original._scenario_techniques},
                    "initial_metadata": {"scenario_expected_role_references_v1": expected},
                }
                if changed:
                    with pytest.raises(ScenarioRunConflictError, match="changed after preflight"):
                        await service._initialize_scenario_async(**kwargs)
                    prepare.assert_not_awaited()
                else:
                    child = await service._initialize_scenario_async(**kwargs)
                    prepare.assert_awaited_once()
                    assert child._scenario_result_id
            results = await CentralMemory.get_memory_instance().get_scenario_results_async()
            assert len(results) == (0 if changed else 1)
            assert not target.prompt_sent
        finally:
            await service.shutdown_async()
