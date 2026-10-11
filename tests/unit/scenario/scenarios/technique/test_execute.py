# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for single-technique execution, history, retries, and resume."""

import importlib
import uuid
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.converter import Base64Converter
from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack, RedTeamingAttack
from pyrit.executor.attack.compound import SequenceCompletionPolicy, SequentialAttack, SequentialAttackResult
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import (
    AtomicAttackEvaluationIdentifier,
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResultRole,
    AttackResultSelection,
    AttackTechniqueSeedGroup,
    BoundedDatasetSize,
    ComponentIdentifier,
    Message,
    MessagePiece,
    RequestTraceContext,
    Score,
)
from pyrit.prompt_target import TargetCapabilities, TargetConfiguration
from pyrit.registry import AttackTechniqueRegistry, ScenarioRegistry, TargetRegistry
from pyrit.scenario import AttackTechniqueFactory, DatasetAttackConfiguration
from pyrit.scenario.core.scenario_target_defaults import override_default_adversarial_target
from pyrit.scenario.scenarios._dynamic_techniques import reset_dynamic_technique_caches
from pyrit.scenario.scenarios.technique.execute import Execute, _build_execute_technique
from pyrit.score import TrueFalseScorer
from pyrit.setup.initializers.techniques import build_technique_factories
from tests.unit.mocks import MockPromptTarget


@pytest.fixture
def catalog(patch_central_database: MagicMock) -> Iterator[AttackTechniqueRegistry]:
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    targets = TargetRegistry.get_registry_singleton()
    targets.instances.register(MockPromptTarget(), name="adversarial_chat")
    targets.instances.register(MockPromptTarget(), name="objective_scorer_chat")
    registry = AttackTechniqueRegistry.get_registry_singleton()
    registry.register_from_factories(build_technique_factories(groups=["core", "extra"]))
    registry.register_from_factories(
        [
            AttackTechniqueFactory(
                name="prompt_sending",
                attack_class=PromptSendingAttack,
                technique_tags=["single_turn"],
                supports_additional_request_converters=True,
            )
        ]
    )
    reset_dynamic_technique_caches()
    yield registry
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    reset_dynamic_technique_caches()


@pytest.fixture
def scorer() -> MagicMock:
    result = MagicMock(spec=TrueFalseScorer)
    result.get_identifier.return_value = ComponentIdentifier(class_name="Scorer", class_module="test")
    result.get_scorer_metrics.return_value = None
    return result


def _scenario(
    *, scorer: TrueFalseScorer, target: MockPromptTarget | None = None, resume_id: str | None = None, **params: Any
) -> Execute:
    scenario = Execute(objective_scorer=scorer, scenario_result_id=resume_id)
    names = params.pop("scenario_techniques", ["prompt_sending"])
    scenario.set_params_from_args(
        args={
            "objective_target": target or MockPromptTarget(),
            "objective": "Describe how to stop a Python process.",
            "scenario_techniques": [scenario._technique_class[name] for name in names],
            **params,
        }
    )
    return scenario


async def _save_history_async(
    *, memory: MemoryInterface, roles: list[str], metadata: dict[str, Any] | None = None
) -> str:
    conversation_id = str(uuid.uuid4())
    for index, role in enumerate(roles):
        await memory.add_message_to_memory_async(
            request=MessagePiece(
                role=role,
                original_value=f"original {index}",
                converted_value=f"received {index}",
                conversation_id=conversation_id,
                sequence=index,
                prompt_metadata=metadata or {},
            ).to_message()
        )
    return conversation_id


@pytest.mark.usefixtures("patch_central_database", "catalog")
class TestExecute:
    def test_registry_and_parameters(self, scorer: MagicMock) -> None:
        registry = ScenarioRegistry.get_registry_singleton()
        assert registry.get_class("technique.execute") is Execute
        with patch.object(Execute, "_get_default_objective_scorer", return_value=scorer):
            metadata = registry.get_class_metadata(Execute)
            scenario = Execute()
        assert metadata.default_techniques == ("red_teaming",)
        assert not metadata.include_baseline_by_default
        assert metadata.uses_default_adversarial_target
        assert metadata.default_datasets == ()
        parameters = {parameter.name: parameter for parameter in scenario.supported_parameters()}
        assert "dataset_config" not in parameters
        assert parameters["objective"].multiline
        assert {"harm_categories", "retries_on_objective_failure", "prepended_conversation_id"} <= parameters.keys()
        with pytest.raises(ValueError, match="unknown parameter.*dataset_config"):
            scenario.set_params_from_args(args={"dataset_config": DatasetAttackConfiguration()})

    @pytest.mark.parametrize("selection", [None, [], ["red_teaming"], ["DEFAULT"]])
    def test_one_default_technique(self, scorer: MagicMock, selection: Any) -> None:
        scenario = Execute(objective_scorer=scorer)
        selection = [scenario._technique_class[name] for name in selection] if selection else selection
        assert [
            technique.value for technique in scenario._resolve_scenario_techniques(scenario_techniques=selection)
        ] == ["red_teaming"]

    @pytest.mark.parametrize("selection", [["red_teaming", "prompt_sending"], ["MULTI_TURN"], ["ALL"]])
    def test_multiple_techniques_rejected(self, scorer: MagicMock, selection: list[str]) -> None:
        scenario = Execute(objective_scorer=scorer)
        with pytest.raises(ValueError, match="exactly one concrete technique"):
            scenario._resolve_scenario_techniques(
                scenario_techniques=[scenario._technique_class[name] for name in selection]
            )

    def test_fallback_default_is_single_and_deterministic(self, scorer: MagicMock) -> None:
        AttackTechniqueRegistry.reset_registry_singleton()
        registry = AttackTechniqueRegistry.get_registry_singleton()
        registry.register_from_factories(
            [AttackTechniqueFactory(name=name, attack_class=PromptSendingAttack) for name in ["z_last", "a_first"]]
        )
        _build_execute_technique.cache_clear()
        scenario = Execute(objective_scorer=scorer)
        assert [technique.value for technique in scenario._resolve_scenario_techniques(scenario_techniques=None)] == [
            "a_first"
        ]
        assert [technique.value for technique in scenario._technique_class.expand({scenario._default_technique})] == [
            "a_first"
        ]

    @pytest.mark.parametrize("objective", [None, "", " \n\t"])
    async def test_objective_required_only_at_initialize_async(self, scorer: MagicMock, objective: str | None) -> None:
        scenario = _scenario(scorer=scorer, objective=objective)
        assert (await scenario.get_run_size_estimate_async()).estimated_attack_count == 1
        with pytest.raises(ValueError, match="non-blank objective"):
            await scenario.initialize_async()

    @pytest.mark.parametrize("baseline", [False, True])
    @pytest.mark.parametrize("retries", [0, 2])
    async def test_estimate_without_history_or_dataset_reads_async(
        self, *, scorer: MagicMock, baseline: bool, retries: int
    ) -> None:
        scenario = _scenario(
            scorer=scorer,
            include_baseline=baseline,
            retries_on_objective_failure=retries,
            prepended_conversation_id="not-loaded-in-preview",
        )
        with (
            patch.object(
                scenario._memory, "get_conversation_messages_async", side_effect=AssertionError("Read history")
            ),
            patch.object(
                DatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                side_effect=AssertionError("Read dataset"),
            ),
        ):
            estimate = await scenario.get_run_size_estimate_async()
        assert estimate.estimated_attack_count == 1 + int(baseline)
        assert estimate.dataset_size == BoundedDatasetSize(value=1)
        assert f"Up to {retries + 1}" in estimate.note

    async def test_default_preview_without_objective_async(self, scorer: MagicMock) -> None:
        scenario = Execute(objective_scorer=scorer)
        assert (await scenario.get_default_run_size_estimate_async()).estimated_attack_count == 1

    async def test_negative_retry_count_rejected_in_preview_async(self, scorer: MagicMock) -> None:
        scenario = _scenario(scorer=scorer, retries_on_objective_failure=-1)
        with pytest.raises(ValueError, match="nonnegative"):
            await scenario.get_run_size_estimate_async()

    @pytest.mark.parametrize("retries", [0, 2])
    async def test_build_paths_apply_converters_categories_and_baseline_async(
        self, *, scorer: MagicMock, retries: int
    ) -> None:
        converter = Base64Converter()
        scenario = _scenario(
            scorer=scorer,
            retries_on_objective_failure=retries,
            technique_converters={"prompt_sending": [converter]},
            harm_categories=["testing"],
            include_baseline=True,
        )
        with (
            patch.object(
                DatasetAttackConfiguration,
                "get_attack_groups_by_dataset_async",
                side_effect=AssertionError("Read dataset"),
            ),
            patch.object(
                AttackTechniqueFactory, "create", autospec=True, side_effect=AttackTechniqueFactory.create
            ) as create,
        ):
            await scenario.initialize_async()
        assert create.call_count == 1
        assert len(scenario._atomic_attacks) == 2
        baseline, atomic = scenario._atomic_attacks
        assert isinstance(baseline.attack_technique.attack, PromptSendingAttack)
        assert atomic.display_group == atomic.technique_name == "prompt_sending"
        assert atomic.seed_groups[0].objective.value == scenario.params["objective"]
        assert atomic.seed_groups[0].objective.harm_categories == ["testing"]
        strategy = atomic.attack_technique.attack
        strategies = [child.strategy for child in strategy._child_attacks] if retries else [strategy]
        assert len(strategies) == retries + 1
        assert len({id(child) for child in strategies}) == 1
        assert all(child._request_converters[0].converters == [converter] for child in strategies)
        if retries:
            assert strategy._completion_policy is SequenceCompletionPolicy.FIRST_SUCCESS
            assert [dict(child.memory_labels) for child in strategy._child_attacks] == [
                {"_objective_attempt": str(index)} for index in range(1, retries + 2)
            ]

    @pytest.mark.parametrize("retries", [0, 2])
    async def test_adversarial_override_and_turn_budget_async(self, *, scorer: MagicMock, retries: int) -> None:
        memory = CentralMemory.get_memory_instance()
        history_id = await _save_history_async(memory=memory, roles=["user", "assistant"] * 12)
        target = MockPromptTarget()
        with override_default_adversarial_target(target):
            scenario = _scenario(
                scorer=scorer,
                scenario_techniques=["red_teaming"],
                prepended_conversation_id=history_id,
                retries_on_objective_failure=retries,
            )
            await scenario.initialize_async()
        strategy = scenario._atomic_attacks[0].attack_technique.attack
        strategies = [child.strategy for child in strategy._child_attacks] if retries else [strategy]
        assert all(child._adversarial_chat is target for child in strategies)
        assert all(child._max_turns == 22 for child in strategies)
        assert (
            "max_turns"
            not in AttackTechniqueRegistry.get_registry_singleton().get_factories()["red_teaming"]._attack_kwargs
        )

    @pytest.mark.parametrize("retries", [0, 2])
    async def test_incompatible_simulated_technique_rejected_async(self, *, scorer: MagicMock, retries: int) -> None:
        history_id = await _save_history_async(memory=CentralMemory.get_memory_instance(), roles=["user", "assistant"])
        scenario = _scenario(
            scorer=scorer,
            scenario_techniques=["crescendo_simulated"],
            prepended_conversation_id=history_id,
            retries_on_objective_failure=retries,
            include_baseline=True,
        )
        with pytest.raises(ValueError, match="no compatible seed group"):
            await scenario.initialize_async()

    async def test_missing_history_rejected_async(self, scorer: MagicMock) -> None:
        scenario = _scenario(scorer=scorer, prepended_conversation_id=str(uuid.uuid4()))
        with pytest.raises(ValueError, match="empty or does not exist"):
            await scenario.initialize_async()

    @pytest.mark.parametrize("error", ["blocked", "processing"])
    async def test_error_history_rejected_async(self, *, scorer: MagicMock, error: str) -> None:
        memory = CentralMemory.get_memory_instance()
        piece = MessagePiece(
            role="assistant", original_value="unavailable", response_error=error, conversation_id=str(uuid.uuid4())
        )
        await memory.add_message_to_memory_async(request=piece.to_message())
        scenario = _scenario(scorer=scorer, prepended_conversation_id=piece.conversation_id)
        with pytest.raises(ValueError, match="blocked or error pieces"):
            await scenario.initialize_async()

    async def test_history_metadata_and_trailing_user_async(self, scorer: MagicMock) -> None:
        memory = CentralMemory.get_memory_instance()
        metadata = {
            "custom": {"name": "kept"},
            RequestTraceContext.REQUEST_METADATA_KEY: {"stale": True},
        }
        history_id = await _save_history_async(
            memory=memory, roles=["user", "assistant", "tool", "user"], metadata=metadata
        )
        before = [
            message.model_dump() for message in await memory.get_conversation_messages_async(conversation_id=history_id)
        ]
        scenario = _scenario(scorer=scorer, prepended_conversation_id=history_id)
        await scenario.initialize_async()
        group = scenario._atomic_attacks[0].seed_groups[0]
        history = group.prepended_conversation
        assert history is not None
        assert [message.api_role for message in history] == ["user", "assistant", "tool"]
        assert [message.get_value() for message in history] == [
            "received 0",
            "received 1",
            "received 2",
        ]
        assert group.next_message.get_value() == "received 3"
        for prompt in group.prompts:
            assert prompt.metadata["custom"] == {"name": "kept"}
            assert RequestTraceContext.REQUEST_METADATA_KEY not in prompt.metadata
        group.prompts[0].metadata["custom"]["name"] = "changed"
        assert [
            message.model_dump() for message in await memory.get_conversation_messages_async(conversation_id=history_id)
        ] == before

    @pytest.mark.parametrize("retries", [0, 2])
    @pytest.mark.parametrize("editable_history", [False, True])
    async def test_execution_copies_history_and_records_attempts_async(
        self, *, scorer: MagicMock, retries: int, editable_history: bool
    ) -> None:
        memory = CentralMemory.get_memory_instance()
        history_id = await _save_history_async(memory=memory, roles=["user", "assistant", "tool"])
        before = [
            message.model_dump() for message in await memory.get_conversation_messages_async(conversation_id=history_id)
        ]
        target = MockPromptTarget(
            custom_configuration=TargetConfiguration(
                capabilities=TargetCapabilities(supports_editable_history=editable_history)
            )
        )
        scenario = _scenario(
            scorer=scorer,
            target=target,
            retries_on_objective_failure=retries,
            prepended_conversation_id=history_id,
            harm_categories=["testing"],
        )
        await scenario.initialize_async()
        # Patch only scoring I/O, leaving preparation, sending, persistence, and attribution real.
        score = Score(
            score_value="false",
            score_type="true_false",
            score_rationale="not achieved",
            scorer_class_identifier=scorer.get_identifier(),
        )
        scorer.score_async.return_value = [score]
        atomic = scenario._atomic_attacks[0]
        atomic.set_scenario_result_id(scenario._scenario_result_id)
        result = await atomic.run_async()
        assert not result.has_incomplete
        envelope = result.completed_results[0]
        attempts = envelope.child_attack_results if retries else [envelope]
        assert len(attempts) == retries + 1
        assert len({attempt.conversation_id for attempt in attempts}) == retries + 1
        assert all(attempt.conversation_id != history_id for attempt in attempts)
        assert all(attempt.targeted_harm_categories == ["testing"] for attempt in attempts)
        assert all(attempt.attribution_parent_id == scenario._scenario_result_id for attempt in attempts)
        if retries:
            assert envelope.attribution_data["result_role"] == AttackResultRole.ORCHESTRATION.value
            assert envelope.targeted_harm_categories == ["testing"]
        for attempt in attempts:
            messages = await memory.get_conversation_messages_async(conversation_id=attempt.conversation_id)
            assert [message.get_piece().role for message in messages[:3]] == [
                "user",
                "simulated_assistant",
                "simulated_tool",
            ]
            assert [message.get_value() for message in messages[:3]] == ["received 0", "received 1", "received 2"]
        assert [
            message.model_dump() for message in await memory.get_conversation_messages_async(conversation_id=history_id)
        ] == before

    async def test_first_success_stops_retry_children_async(self, scorer: MagicMock) -> None:
        target = MockPromptTarget()
        scenario = _scenario(scorer=scorer, target=target, retries_on_objective_failure=3)
        await scenario.initialize_async()
        compound = scenario._atomic_attacks[0].attack_technique.attack
        assert isinstance(compound, SequentialAttack)
        scorer.score_async.side_effect = [
            [
                Score(
                    score_value=value,
                    score_type="true_false",
                    score_rationale="objective verdict",
                    scorer_class_identifier=scorer.get_identifier(),
                )
            ]
            for value in ["false", "true"]
        ]
        result = await compound.execute_async(objective=scenario.params["objective"])
        assert isinstance(result, SequentialAttackResult)
        assert result.outcome is AttackOutcome.SUCCESS
        assert len(target.prompt_sent) == 2
        assert len({child.conversation_id for child in result.child_attack_results}) == 2

    @pytest.mark.parametrize("retries", [0, 2])
    async def test_gui_resume_restores_only_declared_parameters_async(self, *, scorer: MagicMock, retries: int) -> None:
        from pyrit.backend.services.scenario_run_service import ScenarioRunService
        from pyrit.models.catalog.scenario import RunScenarioRequest

        target = MockPromptTarget()
        TargetRegistry.get_registry_singleton().instances.register(target, name="unit_objective")
        memory = CentralMemory.get_memory_instance()
        history_id = await _save_history_async(memory=memory, roles=["user", "assistant"])
        request = RunScenarioRequest(
            scenario_name="technique.execute",
            target_name="unit_objective",
            techniques=["prompt_sending"],
            scenario_params={
                "objective": "Describe how to stop a Python process.",
                "prepended_conversation_id": history_id,
                "retries_on_objective_failure": retries,
            },
            include_baseline=False,
        )
        service = ScenarioRunService()
        try:
            with (
                patch.object(service, "_run_initializers_async", new_callable=AsyncMock),
                patch.object(Execute, "_get_default_objective_scorer", return_value=scorer),
            ):
                prepared = await service._prepare_run_async(request=request)
                stored = (
                    await memory.get_scenario_results_async(
                        scenario_result_ids=[prepared.scenario._scenario_result_id],
                    )
                )[0]
                assert Execute.PREPENDED_CONVERSATION_HASH_KEY in stored.metadata
                assert Execute.PREPENDED_CONVERSATION_HASH_KEY not in stored.scenario_identifier.params
                restored = service._restore_launch_request(stored=stored)
                assert Execute.PREPENDED_CONVERSATION_HASH_KEY not in restored.scenario_params
                resumed = await service._prepare_run_async(request=restored)
                assert resumed.scenario._scenario_result_id == prepared.scenario._scenario_result_id
                await memory.add_message_to_memory_async(
                    request=MessagePiece(
                        role="user",
                        original_value="new turn",
                        conversation_id=history_id,
                    ).to_message()
                )
                with pytest.raises(ValueError, match="does not match the saved history"):
                    await service._prepare_run_async(request=restored)
        finally:
            await service.close_async()

    @pytest.mark.parametrize("retries", [0, 2])
    async def test_packaged_result_identity_matches_direct_execution_async(
        self, *, scorer: MagicMock, retries: int
    ) -> None:
        target = MockPromptTarget()
        scorer.score_async.return_value = [
            Score(
                score_value="false",
                score_type="true_false",
                score_rationale="objective not achieved",
                scorer_class_identifier=scorer.get_identifier(),
            )
        ]
        scenario = _scenario(
            scorer=scorer,
            target=target,
            scenario_techniques=["code_attack_framed"],
            retries_on_objective_failure=retries,
        )
        await scenario.initialize_async()
        atomic = scenario._atomic_attacks[0]
        atomic.set_scenario_result_id(scenario._scenario_result_id)
        results = await atomic.run_async()
        assert not results.has_incomplete
        result = results.completed_results[0]
        attempts = result.child_attack_results if retries else [result]
        technique = (
            AttackTechniqueRegistry.get_registry_singleton()
            .get_factories()["code_attack_framed"]
            .create(objective_target=target, attack_scoring_config=AttackScoringConfig(objective_scorer=scorer))
        )
        expected = AtomicAttackIdentifier.build(
            technique_identifier=technique.get_identifier(),
            seed_group=atomic.seed_groups[0],
        )
        expected_hash = AtomicAttackEvaluationIdentifier(expected).eval_hash
        persisted = await CentralMemory.get_memory_instance().get_attack_results_async(
            attack_result_ids=[attempt.attack_result_id for attempt in attempts],
            result_selection=AttackResultSelection.ALL_RESULTS,
        )
        assert len(persisted) == retries + 1
        for attempt in [*attempts, *persisted]:
            identifier = AtomicAttackIdentifier.from_component_identifier(attempt.atomic_attack_identifier)
            assert identifier.attack_technique.technique_seeds
            assert identifier.logical_seed_group_id == atomic.seed_groups[0].logical_id
            assert identifier.eval_hash == expected_hash
            assert attempt.attribution_parent_id == scenario._scenario_result_id

    async def test_failed_retry_child_retains_packaged_identity_async(self, scorer: MagicMock) -> None:
        target = MockPromptTarget()
        scenario = _scenario(
            scorer=scorer,
            target=target,
            scenario_techniques=["code_attack_framed"],
            retries_on_objective_failure=1,
        )
        await scenario.initialize_async()
        atomic = scenario._atomic_attacks[0]
        atomic.set_scenario_result_id(scenario._scenario_result_id)
        with patch.object(target, "_send_prompt_to_target_async", side_effect=ValueError("mock transport error")):
            results = await atomic.run_async()
        assert results.has_incomplete
        memory = CentralMemory.get_memory_instance()
        envelope = (
            await memory.get_attack_results_async(
                attack_result_ids=results.incomplete_result_ids,
                result_selection=AttackResultSelection.ALL_RESULTS,
            )
        )[0]
        child = (
            await memory.get_attack_results_async(
                attack_result_ids=envelope.metadata["child_attack_result_ids"],
                result_selection=AttackResultSelection.ALL_RESULTS,
            )
        )[0]
        identifier = AtomicAttackIdentifier.from_component_identifier(child.atomic_attack_identifier)
        assert child.outcome is AttackOutcome.ERROR
        assert identifier.attack_technique.technique_seeds
        assert identifier.logical_seed_group_id == atomic.seed_groups[0].logical_id

    async def test_child_identity_write_failure_preserves_links_and_surfaces_error_async(
        self, scorer: MagicMock
    ) -> None:
        target = MockPromptTarget()
        scorer.score_async.return_value = [
            Score(
                score_value="false",
                score_type="true_false",
                score_rationale="objective not achieved",
                scorer_class_identifier=scorer.get_identifier(),
            )
        ]
        scenario = _scenario(scorer=scorer, target=target, retries_on_objective_failure=1)
        await scenario.initialize_async()
        atomic = scenario._atomic_attacks[0]
        memory = CentralMemory.get_memory_instance()
        with patch.object(
            memory, "update_attack_result_by_id_async", side_effect=RuntimeError("identity write failed")
        ):
            results = await atomic.run_async()
        assert results.has_incomplete
        envelope = (
            await memory.get_attack_results_async(
                attack_result_ids=results.incomplete_result_ids,
                result_selection=AttackResultSelection.ALL_RESULTS,
            )
        )[0]
        assert envelope.outcome is AttackOutcome.ERROR
        assert "identity write failed" in envelope.error_message
        children = await memory.get_attack_results_async(
            attack_result_ids=envelope.metadata["child_attack_result_ids"],
            result_selection=AttackResultSelection.ALL_RESULTS,
        )
        assert len(children) == 1

    @pytest.mark.parametrize("retries", [0, 2])
    async def test_resume_same_inputs_and_reject_changed_objective_async(
        self, *, scorer: MagicMock, retries: int
    ) -> None:
        target = MockPromptTarget()
        original = _scenario(scorer=scorer, target=target, retries_on_objective_failure=retries)
        await original.initialize_async()
        resumed = _scenario(
            scorer=scorer, target=target, resume_id=original._scenario_result_id, retries_on_objective_failure=retries
        )
        await resumed.initialize_async()
        assert resumed._atomic_attacks[0].logical_group_id == original._atomic_attacks[0].logical_group_id
        changed = _scenario(
            scorer=scorer,
            target=target,
            resume_id=original._scenario_result_id,
            retries_on_objective_failure=retries,
            objective="Changed objective",
        )
        with pytest.raises(ValueError, match="does not match"):
            await changed.initialize_async()

    @pytest.mark.parametrize("change", ["append", "metadata"])
    async def test_resume_rejects_changed_source_async(self, *, scorer: MagicMock, change: str) -> None:
        memory = CentralMemory.get_memory_instance()
        history_id = await _save_history_async(memory=memory, roles=["user", "assistant"])
        target = MockPromptTarget()
        original = _scenario(scorer=scorer, target=target, prepended_conversation_id=history_id)
        await original.initialize_async()
        resumed = _scenario(
            scorer=scorer, target=target, prepended_conversation_id=history_id, resume_id=original._scenario_result_id
        )
        await resumed.initialize_async()
        if change == "append":
            await memory.add_message_to_memory_async(
                request=MessagePiece(role="user", original_value="new turn", conversation_id=history_id).to_message()
            )
        else:
            messages = await memory.get_conversation_messages_async(conversation_id=history_id)
            messages[0].get_piece().prompt_metadata["custom"] = "new metadata"
            await memory.update_prompt_metadata_by_conversation_id_async(
                conversation_id=history_id, prompt_metadata={"custom": "new metadata"}
            )
        with pytest.raises(ValueError, match="does not match"):
            await resumed.initialize_async()

    def test_alias_and_dynamic_cache_reset(self) -> None:
        package = importlib.import_module("pyrit.scenario.technique")
        module = importlib.import_module("pyrit.scenario.technique.execute")
        assert module.Execute is package.Execute is Execute
        first = package.ExecuteTechnique
        reset_dynamic_technique_caches()
        assert "ExecuteTechnique" not in package.__dict__
        assert package.ExecuteTechnique is not first

    def test_factory_budget_copies_keep_settings(self, scorer: MagicMock) -> None:
        target = MockPromptTarget()
        factory = AttackTechniqueFactory(
            name="custom",
            attack_class=RedTeamingAttack,
            attack_kwargs={"max_turns": 2},
            use_score_as_feedback=False,
        ).with_adversarial_system_prompt_prefix("Keep this guidance.")
        identity = factory.get_identifier()
        copied = factory.with_extra_turns(turns=12)
        attack = copied.create(
            objective_target=target, attack_scoring_config=AttackScoringConfig(objective_scorer=scorer)
        ).attack
        assert attack._max_turns == 14
        assert not copied._use_score_as_feedback
        assert copied._adversarial_system_prompt_prefix == "Keep this guidance."
        assert factory.get_identifier() == identity
        assert copied.get_identifier().hash != identity.hash
        assert factory._attack_kwargs == {"max_turns": 2}
        single_turn = AttackTechniqueFactory(name="single", attack_class=PromptSendingAttack)
        assert single_turn.with_extra_turns(turns=12) is single_turn

    async def test_compound_identity_tracks_children_and_policy_async(self, scorer: MagicMock) -> None:
        plain = _scenario(scorer=scorer, retries_on_objective_failure=1)
        converted = _scenario(
            scorer=scorer, retries_on_objective_failure=1, technique_converters={"prompt_sending": [Base64Converter()]}
        )
        longer = _scenario(scorer=scorer, retries_on_objective_failure=2)
        for scenario in [plain, converted, longer]:
            await scenario.initialize_async()
        hashes = {scenario._atomic_attacks[0].technique_eval_hash for scenario in [plain, converted, longer]}
        assert len(hashes) == 3

    async def test_existing_compound_identity_is_unchanged_without_packaged_children_async(
        self, scorer: MagicMock
    ) -> None:
        from pyrit.executor.attack.compound import SequentialChildAttack

        scenario = _scenario(scorer=scorer)
        await scenario.initialize_async()
        atomic = scenario._atomic_attacks[0]
        compound = SequentialAttack(
            objective_target=atomic.attack_technique.attack.get_objective_target(),
            child_attacks=[
                SequentialChildAttack(
                    strategy=atomic.attack_technique.attack,
                    seed_group=atomic.seed_groups[0],
                )
            ],
        )
        assert compound.get_identifier() == compound._create_identifier()

    async def test_retry_identity_retains_technique_seeds_on_resume_async(self, scorer: MagicMock) -> None:
        factory = AttackTechniqueFactory(
            name="seeded",
            attack_class=PromptSendingAttack,
            seed_technique=AttackTechniqueSeedGroup.from_system_prompt("First framing"),
        )
        AttackTechniqueRegistry.get_registry_singleton().register_from_factories([factory])
        _build_execute_technique.cache_clear()
        target = MockPromptTarget()
        original = _scenario(
            scorer=scorer,
            target=target,
            scenario_techniques=["seeded"],
            retries_on_objective_failure=1,
        )
        await original.initialize_async()
        with patch.object(factory, "_seed_technique", AttackTechniqueSeedGroup.from_system_prompt("Changed framing")):
            resumed = _scenario(
                scorer=scorer,
                target=target,
                scenario_techniques=["seeded"],
                retries_on_objective_failure=1,
                resume_id=original._scenario_result_id,
            )
            with pytest.raises(ValueError, match="cannot resume"):
                await resumed.initialize_async()

    @pytest.mark.parametrize("retries", [0, 1])
    async def test_error_retry_is_separate_from_objective_retry_async(self, *, scorer: MagicMock, retries: int) -> None:
        target = MockPromptTarget()
        scenario = _scenario(scorer=scorer, target=target, retries_on_objective_failure=retries, max_retries=1)
        await scenario.initialize_async()
        score = Score(
            score_value="false",
            score_type="true_false",
            score_rationale="not achieved",
            scorer_class_identifier=scorer.get_identifier(),
        )
        scorer.score_async.return_value = [score]
        send = target._send_prompt_to_target_async
        calls = 0

        async def fail_once_async(*, normalized_conversation: list[Message]) -> list[Message]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ValueError("mock transport failure")
            responses = await send(normalized_conversation=normalized_conversation)
            assert isinstance(responses, list)
            result: list[Message] = []
            for response in responses:
                assert isinstance(response, Message)
                result.append(response)
            return result

        with patch.object(target, "_send_prompt_to_target_async", side_effect=fail_once_async):
            result = await scenario.run_async()
        assert calls == retries + 2
        assert result.attack_results["prompt_sending_objective"][-1].outcome is AttackOutcome.FAILURE

    @pytest.mark.parametrize("retries", [0, 1])
    async def test_completed_run_resume_does_not_repeat_attempts_async(
        self, *, scorer: MagicMock, retries: int
    ) -> None:
        target = MockPromptTarget()
        scorer.score_async.return_value = [
            Score(
                score_value="false",
                score_type="true_false",
                score_rationale="not achieved",
                scorer_class_identifier=scorer.get_identifier(),
            )
        ]
        original = _scenario(
            scorer=scorer,
            target=target,
            retries_on_objective_failure=retries,
            include_baseline=True,
        )
        await original.initialize_async()
        await original.run_async()
        assert len(target.prompt_sent) == retries + 2
        resumed = _scenario(
            scorer=scorer,
            target=target,
            retries_on_objective_failure=retries,
            include_baseline=True,
            resume_id=original._scenario_result_id,
        )
        await resumed.initialize_async()
        await resumed.run_async()
        assert len(target.prompt_sent) == retries + 2

    @pytest.mark.parametrize("name", ["technique.execute", "garak.exploitation"])
    def test_e2e_argv_omits_dataset_limits_for_objective_only_scenarios(
        self, *, name: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from tests.end_to_end import test_scenarios

        with patch.object(test_scenarios, "pyrit_scan_main", return_value=0) as scan:
            test_scenarios.test_scenario_with_pyrit_scan(scenario_name=name, capsys=capsys)
        arguments = scan.call_args.args[0]
        assert "--max-dataset-size" not in arguments
        if name == "technique.execute":
            assert "--objective" in arguments
            assert arguments[arguments.index("--techniques") + 1] == "red_teaming"

    @pytest.mark.parametrize("last_role", ["assistant", "user", "tool"])
    async def test_history_does_not_consume_new_automated_turns_async(
        self, *, scorer: MagicMock, last_role: str
    ) -> None:
        memory = CentralMemory.get_memory_instance()
        roles = ["user", "assistant"] * 12
        if last_role != "assistant":
            roles.append(last_role)
        history_id = await _save_history_async(memory=memory, roles=roles)
        registry = AttackTechniqueRegistry.get_registry_singleton()
        registry.register_from_factories(
            [
                AttackTechniqueFactory(
                    name="short_red_teaming", attack_class=RedTeamingAttack, attack_kwargs={"max_turns": 2}
                )
            ]
        )
        _build_execute_technique.cache_clear()
        target = MockPromptTarget()
        scenario = _scenario(
            scorer=scorer,
            target=target,
            scenario_techniques=["short_red_teaming"],
            prepended_conversation_id=history_id,
        )
        await scenario.initialize_async()
        attack = scenario._atomic_attacks[0].attack_technique.attack
        score = Score(
            score_value="false",
            score_type="true_false",
            score_rationale="not achieved",
            scorer_class_identifier=scorer.get_identifier(),
        )
        with (
            patch.object(
                attack,
                "_generate_next_prompt_async",
                new_callable=AsyncMock,
                side_effect=lambda **kwargs: Message.from_prompt(prompt="next automated turn", role="user"),
            ),
            patch.object(attack, "_score_response_async", new_callable=AsyncMock, return_value=score),
        ):
            result = await scenario._atomic_attacks[0].run_async()
        assert not result.has_incomplete
        assert len(target.prompt_sent) == 2
        assert result.completed_results[0].executed_turns == 14

    def test_factory_budget_uses_configured_global_default(self, scorer: MagicMock) -> None:
        from pyrit.common import get_global_default_values

        factory = AttackTechniqueFactory(name="red", attack_class=RedTeamingAttack)
        with patch.object(get_global_default_values(), "get_default_value", return_value=(True, 3)):
            extended = factory.with_extra_turns(turns=12)
        assert extended._attack_kwargs["max_turns"] == 15
        with pytest.raises(ValueError, match="nonnegative"):
            factory.with_extra_turns(turns=-1)

    async def test_tool_payloads_multipart_history_and_metadata_are_preserved_async(self, scorer: MagicMock) -> None:
        memory = CentralMemory.get_memory_instance()
        conversation_id = str(uuid.uuid4())
        call = '{"id":"call-1","function":{"name":"lookup","arguments":"{}"},"type":"function"}'
        output = '{"call_id":"call-1","output":"done"}'
        messages = [
            Message.from_prompt(prompt="look up the value", role="user"),
            Message(
                message_pieces=[
                    MessagePiece(role="assistant", original_value=call, original_value_data_type="function_call"),
                    MessagePiece(role="assistant", original_value="working", original_value_data_type="reasoning"),
                ]
            ),
            MessagePiece(
                role="tool",
                original_value=output,
                original_value_data_type="function_call_output",
                prompt_metadata={"provider": {"call_id": "call-1"}},
            ).to_message(),
        ]
        for message in messages:
            for piece in message.message_pieces:
                piece.conversation_id = conversation_id
            await memory.add_message_to_memory_async(request=message)
        scenario = _scenario(scorer=scorer, prepended_conversation_id=conversation_id)
        await scenario.initialize_async()
        history = scenario._atomic_attacks[0].seed_groups[0].prepended_conversation
        assert history is not None
        assert len(history) == 3
        assert [piece.converted_value_data_type for piece in history[1].message_pieces] == [
            "function_call",
            "reasoning",
        ]
        assert history[1].message_pieces[0].converted_value == call
        assert history[2].get_piece().converted_value == output
        assert history[2].get_piece().role == "simulated_tool"
        assert history[2].get_piece().prompt_metadata["provider"] == {"call_id": "call-1"}
