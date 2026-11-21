# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""CBRN uses mocked populations and synthetic identifiers, never benchmark prompts."""

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.executor.attack import PromptSendingAttack, RedTeamingAttack, RepeatedAttack, TAPAttack
from pyrit.memory import CentralMemory
from pyrit.models import (
    AttackOutcome,
    AttackSeedGroup,
    AttackTechniqueSeedGroup,
    Message,
    MessagePiece,
    SeedObjective,
)
from pyrit.models.catalog import RunScenarioRequest
from pyrit.registry import AttackTechniqueRegistry, ScenarioRegistry, ScorerRegistry, TargetRegistry
from pyrit.scenario import (
    AttackTechnique,
    AttackTechniqueFactory,
    DatasetAttackConfiguration,
    IncompatibleTechniqueError,
    Scenario,
)
from pyrit.scenario.scenarios import airt
from pyrit.scenario.scenarios.airt import CBRN
from pyrit.scenario.scenarios.airt.cbrn import _build_cbrn_technique
from pyrit.score import (
    FloatScaleScorer,
    SelfAskRefusalScorer,
    SelfAskTrueFalseScorer,
    SubStringScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseQuestion,
)


def _judge_mock(*, refusal: bool, operational: bool) -> AsyncMock:
    async def judge_async(*, normalized_conversation: list[Message]) -> list[Message]:
        operational_question = any(
            "OPERATIONAL" in piece.converted_value
            for message in normalized_conversation
            for piece in message.message_pieces
            if piece.api_role == "system"
        )
        return [
            MessagePiece(
                role="assistant",
                original_value=json.dumps(
                    {
                        "score_value": operational if operational_question else refusal,
                        "description": "fixture",
                        "rationale": "fixture operational" if operational_question else "fixture refusal",
                        "metadata": "",
                    }
                ),
            ).to_message()
        ]

    return AsyncMock(side_effect=judge_async)


def _labelled_groups(
    dataset_name: str = "airt_cbrn", *, metadata_field: str = "expected"
) -> dict[str, list[AttackSeedGroup]]:
    return {
        dataset_name: [
            AttackSeedGroup(
                seeds=[SeedObjective(value=f"fixture_{role.lower()}-objective", metadata={metadata_field: role})]
            )
            for role in ("ALLOW", "REFUSE")
        ]
    }


@pytest.fixture(autouse=True)
def catalog(patch_central_database: object) -> Iterator[AttackTechniqueRegistry]:
    AttackTechniqueRegistry.reset_registry_singleton()
    ScorerRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    registry = AttackTechniqueRegistry.get_registry_singleton()
    registry.register_from_factories(
        [
            AttackTechniqueFactory(
                name="fixture_attack",
                attack_class=PromptSendingAttack,
                seed_technique=AttackTechniqueSeedGroup.from_system_prompt("Use the fixture response format."),
                technique_tags=["single_turn"],
            )
        ]
    )
    yield registry
    AttackTechniqueRegistry.reset_registry_singleton()
    ScorerRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_cbrn_technique.cache_clear()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("refusal", [True, False])
@pytest.mark.parametrize("operational", [True, False])
async def test_cbrn_runs_both_populations_with_their_own_rubrics_async(refusal: bool, operational: bool) -> None:
    target = MockPromptTarget()
    judge = MockPromptTarget(id="judge")
    scenario = CBRN()
    scenario.set_params_from_args(
        args={
            "objective_target": target,
            "scoring_target": judge,
            "dataset_overrides": {"sources": [{"name": "fixture_combined"}]},
        }
    )
    groups = _labelled_groups("fixture_combined")
    with (
        patch.object(DatasetAttackConfiguration, "prepare_async", AsyncMock()),
        patch.object(Scenario, "_resolve_seed_groups_by_dataset_async", AsyncMock(return_value=groups)),
    ):
        await scenario.initialize_async()

    assert len(scenario._atomic_attacks) == 2
    assert isinstance(scenario._objective_scorer, SelfAskRefusalScorer)
    assert all(isinstance(atomic.attack_technique.attack, RepeatedAttack) for atomic in scenario._atomic_attacks)
    for atomic in scenario._atomic_attacks:
        repeated = atomic.attack_technique.attack
        assert isinstance(repeated._child_attacks[0].strategy, PromptSendingAttack)
        assert len(repeated._child_attacks) == 5
    with patch.object(
        judge, "_send_prompt_to_target_async", _judge_mock(refusal=refusal, operational=operational)
    ) as score:
        result = await scenario.run_async()
    assert score.await_count == 15

    parents = [
        row
        for rows in result.attack_results.values()
        for row in rows
        if row.metadata.get(RepeatedAttack.CHILD_ATTACK_RESULT_IDS_KEY)
    ]
    assert len(parents) == 2
    assert len(target.prompt_sent) == 10
    by_objective = {row.objective: row for row in parents}
    for role, success in (("allow", refusal), ("refuse", not refusal and operational)):
        parent = by_objective[f"fixture_{role}-objective"]
        assert parent.outcome is (AttackOutcome.SUCCESS if success else AttackOutcome.FAILURE)
        assert parent.metadata["independent_trials"]["attack_success_rate"] == int(success)
        assert all(trial["score_value"] is success for trial in parent.metadata["independent_trials"]["trials"])

    refuse_child = next(
        row
        for rows in result.attack_results.values()
        for row in rows
        if row.objective == "fixture_refuse-objective" and row.automated_score is not None
    )
    assert refuse_child.last_response is not None
    scores = await CentralMemory.get_memory_instance().get_prompt_scores_async(
        prompt_ids=[str(refuse_child.last_response.id)], include_intermediate=True
    )
    assert len(scores) == 4
    verdicts = {score.scorer_class_identifier.class_name: score.get_value() for score in scores}
    assert verdicts == {
        "SelfAskRefusalScorer": refusal,
        "TrueFalseInverterScorer": not refusal,
        "SelfAskTrueFalseScorer": operational,
        "TrueFalseCompositeScorer": not refusal and operational,
    }


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({"num_trials": 0}, "positive integer"),
        ({"dataset_overrides": {"sources": [{"name": "one"}, {"name": "two"}]}}, "exactly one"),
        ({"dataset_overrides": {"max_per_dataset": 1}}, "at least 2"),
        ({"dataset_overrides": {"max_total": 1}}, "at least 2"),
        ({"include_baseline": True}, "does not support"),
        ({"expected_response_metadata_field": ""}, "non-empty metadata key"),
        ({"expected_response_metadata_field": "   "}, "non-empty metadata key"),
    ],
)
def test_invalid_configuration_rejected_before_execution(params: dict[str, object], error: str) -> None:
    scenario = CBRN()
    scenario.set_params_from_args(args=params)
    with pytest.raises(ValueError, match=error):
        scenario._resolve_runtime_configuration(require_objective_target=False)


@pytest.mark.usefixtures("patch_central_database")
async def test_custom_trial_count_and_estimate_do_not_send_requests_async() -> None:
    scenario = CBRN()
    scenario.set_params_from_args(args={"num_trials": 3})
    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.effective_parameters["num_trials"] == 3
    assert estimate.note is not None
    assert "3 independent trials" in estimate.note
    metadata = ScenarioRegistry().get_class_metadata(CBRN)
    assert metadata.class_name == "CBRN"
    assert metadata.registry_name == "airt.cbrn"
    assert metadata.default_techniques == ("prompt_sending",)
    assert not metadata.uses_default_adversarial_target
    parameters = {parameter.name: parameter for parameter in metadata.supported_parameters}
    assert parameters["expected_response_metadata_field"].default == "expected"


@pytest.mark.usefixtures("patch_central_database")
async def test_empty_technique_selection_uses_the_existing_default_async() -> None:
    scenario = CBRN()
    scenario.set_params_from_args(args={"scenario_techniques": []})
    await scenario.get_run_size_estimate_async()
    assert [technique.value for technique in scenario._scenario_techniques] == ["prompt_sending"]


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("metadata_field", [None, "expected_response", "custom_label"])
async def test_expected_response_metadata_field_selects_population_scorers_async(metadata_field: str | None) -> None:
    field = metadata_field or "expected"
    scenario = CBRN()
    scenario.set_params_from_args(
        args={
            "objective_target": MockPromptTarget(),
            "scoring_target": MockPromptTarget(id="judge"),
            **({"expected_response_metadata_field": field} if metadata_field is not None else {}),
        }
    )
    groups = _labelled_groups(metadata_field=field)
    for group in groups["airt_cbrn"]:
        group.objective.metadata["expected_response" if field == "expected" else "expected"] = (
            "REFUSE" if group.objective.metadata[field] == "ALLOW" else "ALLOW"
        )
    with (
        patch.object(DatasetAttackConfiguration, "prepare_async", AsyncMock()),
        patch.object(Scenario, "_resolve_seed_groups_by_dataset_async", AsyncMock(return_value=groups)),
    ):
        await scenario.initialize_async()
    assert [atomic.display_group for atomic in scenario._atomic_attacks] == ["ALLOW", "REFUSE"]
    scorers = [atomic.attack_technique.attack._child_attacks[0].objective_scorer for atomic in scenario._atomic_attacks]
    assert scorers[0] is scenario._objective_scorer
    assert isinstance(scorers[1], TrueFalseCompositeScorer)
    assert scenario._build_scenario_identifier().params["expected_response_metadata_field"] == field
    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.effective_parameters["expected_response_metadata_field"] == field


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("metadata_field", ["expected", "custom_label"])
async def test_missing_selected_metadata_field_does_not_fall_back_async(metadata_field: str) -> None:
    scenario = CBRN()
    scenario.set_params_from_args(args={"expected_response_metadata_field": metadata_field})
    scenario._resolve_runtime_configuration(require_objective_target=False)
    groups = _labelled_groups(metadata_field="expected_response" if metadata_field == "expected" else "expected")
    with (
        patch.object(Scenario, "_resolve_seed_groups_by_dataset_async", AsyncMock(return_value=groups)),
        pytest.raises(ValueError, match=f"metadata\\.{metadata_field}"),
    ):
        await scenario._resolve_seed_groups_by_dataset_async()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("source_cap", "total_cap"), [(3, 2), (2, "all")])
async def test_sampling_preserves_both_populations_under_a_total_cap_async(
    source_cap: int, total_cap: int | str
) -> None:
    scenario = CBRN()
    scenario.set_params_from_args(args={"dataset_overrides": {"max_per_dataset": source_cap, "max_total": total_cap}})
    scenario._resolve_runtime_configuration(require_objective_target=False)
    groups = _labelled_groups()
    groups["airt_cbrn"].extend(
        AttackSeedGroup(seeds=[SeedObjective(value=f"extra-{index}", metadata={"expected": "ALLOW"})])
        for index in range(20)
    )
    with patch.object(Scenario, "_resolve_seed_groups_by_dataset_async", AsyncMock(return_value=groups)):
        selected = await scenario._resolve_seed_groups_by_dataset_async()
    assert len(selected["airt_cbrn"]) == 2
    assert {group.objective.metadata["expected"] for group in selected["airt_cbrn"]} == {"ALLOW", "REFUSE"}
    assert all(any(group is original for original in groups["airt_cbrn"]) for group in selected["airt_cbrn"])


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("label", [None, "", "allow", "UNKNOWN", True])
@pytest.mark.parametrize("metadata_field", ["expected", "custom_label"])
async def test_invalid_expected_response_is_rejected_before_sampling_async(label: object, metadata_field: str) -> None:
    scenario = CBRN()
    scenario.set_params_from_args(
        args={"dataset_overrides": {"max_per_dataset": 2}, "expected_response_metadata_field": metadata_field}
    )
    scenario._resolve_runtime_configuration(require_objective_target=False)
    groups = _labelled_groups(metadata_field=metadata_field)
    groups["airt_cbrn"].append(
        AttackSeedGroup(seeds=[SeedObjective(value="invalid-label", metadata={metadata_field: label})])
    )
    with (
        patch.object(Scenario, "_resolve_seed_groups_by_dataset_async", AsyncMock(return_value=groups)),
        pytest.raises(ValueError, match=f"metadata\\.{metadata_field}"),
    ):
        await scenario._resolve_seed_groups_by_dataset_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_dataset_must_include_both_expected_response_populations_async() -> None:
    scenario = CBRN()
    scenario._resolve_runtime_configuration(require_objective_target=False)
    groups = _labelled_groups()
    groups["airt_cbrn"].pop()
    with (
        patch.object(Scenario, "_resolve_seed_groups_by_dataset_async", AsyncMock(return_value=groups)),
        pytest.raises(ValueError, match="No attack groups.*REFUSE"),
    ):
        await scenario._resolve_seed_groups_by_dataset_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_resume_rejects_expected_response_label_changes_async() -> None:
    scorer = SubStringScorer(substring="fixture refusal")
    target = MockPromptTarget()
    params = {"objective_target": target, "num_trials": 1, "scoring_target": MockPromptTarget(id="judge")}
    scenario = CBRN(objective_scorer=scorer)
    scenario.set_params_from_args(args=params)
    groups = _labelled_groups()
    with (
        patch.object(DatasetAttackConfiguration, "prepare_async", AsyncMock()),
        patch.object(Scenario, "_resolve_seed_groups_by_dataset_async", AsyncMock(return_value=groups)),
    ):
        await scenario.initialize_async()
        resumed = CBRN(objective_scorer=scorer, scenario_result_id=scenario._scenario_result_id)
        resumed.set_params_from_args(args=params)
        await resumed.initialize_async()
        assert [atomic.atomic_attack_name for atomic in resumed._atomic_attacks] == [
            atomic.atomic_attack_name for atomic in scenario._atomic_attacks
        ]
        groups["airt_cbrn"][0].objective.metadata["expected"] = "REFUSE"
        changed = CBRN(objective_scorer=scorer, scenario_result_id=scenario._scenario_result_id)
        changed.set_params_from_args(args=params)
        with pytest.raises(ValueError, match="planned atomic group.*no longer reconstructable"):
            await changed.initialize_async()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("dependency", ["rubric", "judge"])
async def test_resume_rejects_operational_dependency_changes_async(dependency: str) -> None:
    class DifferentJudge(MockPromptTarget):
        pass

    scorer = SubStringScorer(substring="fixture refusal")
    params = {
        "objective_target": MockPromptTarget(),
        "scoring_target": MockPromptTarget(id="judge"),
        "num_trials": 1,
    }
    scenario = CBRN(objective_scorer=scorer)
    scenario.set_params_from_args(args=params)
    with (
        patch.object(DatasetAttackConfiguration, "prepare_async", AsyncMock()),
        patch.object(Scenario, "_resolve_seed_groups_by_dataset_async", AsyncMock(return_value=_labelled_groups())),
    ):
        await scenario.initialize_async()
        resumed = CBRN(objective_scorer=scorer, scenario_result_id=scenario._scenario_result_id)
        changed_params = {**params}
        if dependency == "judge":
            changed_params["scoring_target"] = DifferentJudge()
        resumed.set_params_from_args(args=changed_params)
        original_loader = TrueFalseQuestion.from_yaml

        def changed_question(path: str | Path) -> TrueFalseQuestion:
            question = original_loader(path)
            return (
                question.model_copy(update={"true_description": "Changed operational criterion"})
                if dependency == "rubric"
                else question
            )

        with (
            patch.object(TrueFalseQuestion, "from_yaml", side_effect=changed_question),
            pytest.raises(ValueError, match="saved scenario dependencies changed"),
        ):
            await resumed.initialize_async()


@pytest.mark.usefixtures("patch_central_database")
def test_operational_judge_reset_and_missing_target_readiness() -> None:
    scenario = CBRN(objective_scorer=SubStringScorer(substring="fixture refusal"))
    params = {"objective_target": MockPromptTarget(), "scoring_target": MockPromptTarget(id="judge")}
    scenario.set_params_from_args(args=params)
    assert scenario.get_readiness().ready
    before = scenario._get_refuse_scorer()
    scenario.set_params_from_args(args={**params, "scoring_target": MockPromptTarget(id="different-judge")})
    assert scenario.get_readiness().ready
    assert scenario._get_refuse_scorer() is not before

    scenario.set_params_from_args(args={"objective_target": params["objective_target"]})
    with patch.object(scenario, "_resolve_scoring_target", side_effect=ValueError("No scoring model configured")):
        readiness = scenario.get_readiness()
    assert not readiness.ready
    assert readiness.issues[0].role == "scoring_target"
    assert "No scoring model configured" in readiness.issues[0].reason


@pytest.mark.usefixtures("patch_central_database")
async def test_selected_registry_techniques_keep_trial_counts_scorers_and_seeds_async() -> None:
    target = MockPromptTarget()
    judge = MockPromptTarget(id="judge")
    scenario = CBRN()
    scenario.set_params_from_args(
        args={
            "objective_target": target,
            "scoring_target": judge,
            "scenario_techniques": [scenario._technique_class.ALL],
            "num_trials": 2,
        }
    )
    groups = _labelled_groups()
    with (
        patch.object(DatasetAttackConfiguration, "prepare_async", AsyncMock()),
        patch.object(Scenario, "_resolve_seed_groups_by_dataset_async", AsyncMock(return_value=groups)),
    ):
        await scenario.initialize_async()

    assert len(scenario._atomic_attacks) == 4
    assert len({atomic.atomic_attack_name for atomic in scenario._atomic_attacks}) == 4
    for atomic in scenario._atomic_attacks:
        repeated = atomic.attack_technique.attack
        assert isinstance(repeated, RepeatedAttack)
        assert len(repeated._child_attacks) == 2
        role = "allow" if atomic.atomic_attack_name.startswith("allow_") else "refuse"
        for child in repeated._child_attacks:
            config = child.strategy.get_attack_scoring_config()
            assert config is not None
            if role == "allow":
                assert config.objective_scorer is scenario._objective_scorer
            else:
                assert isinstance(config.objective_scorer, TrueFalseCompositeScorer)
                assert config.objective_scorer._scorers[0]._scorer is scenario._objective_scorer
            assert child.identity_seed_group is groups["airt_cbrn"][0 if role == "allow" else 1]
            if atomic.technique_name == "fixture_attack":
                assert child.seed_group.prepended_conversation
                assert (
                    child.seed_group.prepended_conversation[0].message_pieces[0].original_value
                    == "Use the fixture response format."
                )
    with patch.object(judge, "_send_prompt_to_target_async", _judge_mock(refusal=True, operational=True)):
        result = await scenario.run_async()

    parents = [row for rows in result.attack_results.values() for row in rows if "independent_trials" in row.metadata]
    assert len(parents) == 4
    assert len(target.prompt_sent) == 8
    assert sum(row.outcome is AttackOutcome.SUCCESS for row in parents) == 2
    assert sum(row.outcome is AttackOutcome.FAILURE for row in parents) == 2
    assert all(row.metadata["independent_trials"]["num_trials"] == 2 for row in parents)


@pytest.mark.usefixtures("patch_central_database")
async def test_estimate_counts_selected_techniques_without_expanding_default_async() -> None:
    scenario = CBRN()
    scenario.set_params_from_args(args={"num_trials": 3})
    default_estimate = await scenario.get_run_size_estimate_async()
    assert default_estimate.total_attack_count == 10
    scenario.set_params_from_args(args={"num_trials": 3, "scenario_techniques": [scenario._technique_class.ALL]})
    all_estimate = await scenario.get_run_size_estimate_async()
    assert all_estimate.total_attack_count == 20
    assert all_estimate.note is not None
    assert "60 target-facing trials" in all_estimate.note


@pytest.mark.usefixtures("patch_central_database")
def test_catalog_updates_without_changing_direct_send_default(catalog: AttackTechniqueRegistry) -> None:
    before = airt.CBRNTechnique
    catalog.register_from_factories([AttackTechniqueFactory(name="another_fixture", attack_class=PromptSendingAttack)])
    after = airt.CBRNTechnique
    assert after is not before
    assert {member.value for member in after.get_all_techniques()} == {
        "prompt_sending",
        "fixture_attack",
        "another_fixture",
    }
    assert [member.value for member in after.expand({after.default()})] == ["prompt_sending"]
    assert _build_cbrn_technique() is after


@pytest.mark.usefixtures("patch_central_database")
async def test_adversarial_technique_resolves_its_target_and_keeps_population_scorers_async(
    catalog: AttackTechniqueRegistry,
) -> None:
    adversarial = MockPromptTarget(id="adversarial")
    TargetRegistry.get_registry_singleton().instances.register(adversarial, name="adversarial_chat")
    catalog.register_from_factories(
        [
            AttackTechniqueFactory(
                name="fixture_adversarial",
                attack_class=RedTeamingAttack,
                attack_kwargs={"max_turns": 1},
            )
        ]
    )
    scenario = CBRN()
    scenario.set_params_from_args(
        args={
            "objective_target": MockPromptTarget(),
            "scoring_target": MockPromptTarget(id="judge"),
            "scenario_techniques": [scenario._technique_class["fixture_adversarial"]],
            "num_trials": 2,
        }
    )
    groups = _labelled_groups()
    with (
        patch.object(DatasetAttackConfiguration, "prepare_async", AsyncMock()),
        patch.object(Scenario, "_resolve_seed_groups_by_dataset_async", AsyncMock(return_value=groups)),
    ):
        await scenario.initialize_async()

    assert scenario.uses_default_adversarial_target
    assert len(scenario._atomic_attacks) == 2
    for atomic in scenario._atomic_attacks:
        repeated = atomic.attack_technique.attack
        assert isinstance(repeated, RepeatedAttack)
        assert len(repeated._child_attacks) == 2
        for child in repeated._child_attacks:
            assert isinstance(child.strategy, RedTeamingAttack)
            config = child.strategy.get_attack_adversarial_config()
            assert config is not None
            assert config.target is adversarial


@pytest.mark.usefixtures("patch_central_database")
async def test_registry_technique_cannot_silently_discard_population_scorer_async(
    catalog: AttackTechniqueRegistry,
) -> None:
    scenario = CBRN()
    target = MockPromptTarget()
    scenario.set_params_from_args(
        args={
            "objective_target": target,
            "scoring_target": MockPromptTarget(id="judge"),
            "scenario_techniques": [scenario._technique_class["fixture_attack"]],
        }
    )
    groups = _labelled_groups()
    factory = catalog.get_factories()["fixture_attack"]
    with (
        patch.object(DatasetAttackConfiguration, "prepare_async", AsyncMock()),
        patch.object(Scenario, "_resolve_seed_groups_by_dataset_async", AsyncMock(return_value=groups)),
        patch.object(
            factory, "create", return_value=AttackTechnique(attack=PromptSendingAttack(objective_target=target))
        ),
        pytest.raises(IncompatibleTechniqueError, match="must accept the ALLOW population's objective scorer"),
    ):
        await scenario.initialize_async()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("uses_judge", [False, True])
async def test_registered_objective_scorer_is_shared_and_inverted_by_metadata_async(uses_judge: bool) -> None:
    allow = (
        SelfAskRefusalScorer(chat_target=MockPromptTarget(id="custom-judge"))
        if uses_judge
        else SubStringScorer(substring="fixture refusal")
    )
    registry = ScorerRegistry.get_registry_singleton()
    registry.instances.register(allow, name="fixture_refusal")
    request = RunScenarioRequest.model_validate(
        {
            "scenario_name": "airt.cbrn",
            "target_name": "fixture",
            "objective_scorer": {
                "registry_name": "fixture_refusal",
                "identifier_hash": allow.get_identifier().hash,
            },
            "scenario_params": {"num_trials": 1},
        }
    )
    kwargs = ScenarioConfigurationResolver.resolve_constructor_kwargs(
        scenario_class=CBRN, objective_scorer=request.objective_scorer
    )
    scenario = CBRN(**kwargs)
    fallback = MockPromptTarget(id="fallback-judge")
    scenario.set_params_from_args(
        args={
            **(request.scenario_params or {}),
            "objective_target": MockPromptTarget(),
            "scoring_target": fallback,
        }
    )
    groups = _labelled_groups()
    with (
        patch.object(scenario, "_resolve_scoring_target", return_value=fallback) as resolve_target,
        patch.object(DatasetAttackConfiguration, "prepare_async", AsyncMock()),
        patch.object(Scenario, "_resolve_seed_groups_by_dataset_async", AsyncMock(return_value=groups)),
    ):
        await scenario.initialize_async()
        readiness = scenario.get_readiness()
    assert not readiness.issues
    assert scenario._objective_scorer is allow
    for atomic in scenario._atomic_attacks:
        repeated = atomic.attack_technique.attack
        assert isinstance(repeated, RepeatedAttack)
        child = repeated._child_attacks[0]
        if atomic.display_group == "ALLOW":
            assert child.objective_scorer is allow
        else:
            assert isinstance(child.objective_scorer, TrueFalseCompositeScorer)
            inverted, operational = child.objective_scorer._scorers
            assert isinstance(inverted, TrueFalseInverterScorer)
            assert inverted._scorer is allow
            assert isinstance(operational, SelfAskTrueFalseScorer)
            assert operational.get_chat_target() is (allow.get_chat_target() if uses_judge else fallback)
        scoring_config = child.strategy.get_attack_scoring_config()
        assert scoring_config is not None
        assert scoring_config.objective_scorer is child.objective_scorer
    role_hashes = {binding.role: binding.identifier.hash for binding in readiness.bindings}
    assert role_hashes["objective_scorer"] == allow.get_identifier().hash
    assert "objective_scorer:refuse" in role_hashes
    assert resolve_target.call_count == (0 if uses_judge else 1)
    identifier = scenario._build_scenario_identifier()
    assert identifier.objective_scorer is not None
    assert identifier.objective_scorer.hash == allow.get_identifier().hash


@pytest.mark.usefixtures("patch_central_database")
def test_objective_scorer_override_is_optional_and_survives_parameter_reset() -> None:
    allow = SubStringScorer(substring="fixture refusal")
    scenario = CBRN(objective_scorer=allow)
    scenario.set_params_from_args(args={"num_trials": 1})
    assert scenario._objective_scorer is allow
    default = CBRN()
    default.set_params_from_args(args={"scoring_target": MockPromptTarget()})
    assert isinstance(default._objective_scorer, SelfAskRefusalScorer)


@pytest.mark.usefixtures("patch_central_database")
def test_objective_scorer_rejects_incompatible_types() -> None:
    with pytest.raises(ValueError, match="objective_scorer must be a TrueFalseScorer"):
        CBRN(objective_scorer=MagicMock(spec=FloatScaleScorer))


@pytest.mark.usefixtures("patch_central_database")
def test_catalog_exposes_scorer_parameters_and_disables_incompatible_techniques(
    catalog: AttackTechniqueRegistry,
) -> None:
    catalog.register_from_factories([AttackTechniqueFactory(name="fixture_tap", attack_class=TAPAttack)])
    with patch.object(CBRN, "_resolve_scoring_target", side_effect=AssertionError("Catalog needs no judge")):
        metadata = ScenarioRegistry().get_class_metadata(CBRN)
    summaries = {summary.name: summary for summary in metadata.technique_summaries}
    reason = summaries["fixture_tap"].unavailable_reason
    assert reason is not None and "TAPAttackScoringConfig" in reason
    assert summaries["prompt_sending"].unavailable_reason is None
    parameters = {
        parameter.name: parameter.for_external_catalog().model_dump() for parameter in metadata.supported_parameters
    }
    assert metadata.supports_objective_scorer
    assert not {"allow_scorer", "refuse_scorer", "allow_dataset", "refuse_dataset"} & parameters.keys()
    assert metadata.default_datasets == ("airt_cbrn",)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("policy", ["skip", "raise"])
async def test_incompatible_scorer_technique_applies_policy_before_readiness_async(
    catalog: AttackTechniqueRegistry, policy: str
) -> None:
    catalog.register_from_factories([AttackTechniqueFactory(name="fixture_tap", attack_class=TAPAttack)])
    scenario = CBRN()
    scenario.set_params_from_args(
        args={"scenario_techniques": [scenario._technique_class.ALL], "incompatible_technique_policy": policy}
    )
    if policy == "raise":
        with pytest.raises(IncompatibleTechniqueError, match="fixture_tap.*incompatible"):
            await scenario.get_run_size_estimate_async()
    else:
        estimate = await scenario.get_run_size_estimate_async()
        assert {technique.value for technique in scenario._scenario_techniques} == {"prompt_sending", "fixture_attack"}
        assert estimate.total_attack_count == 20
        assert not scenario._requires_default_adversarial_target()
        scenario.set_params_from_args(args={"scenario_techniques": [scenario._technique_class["fixture_tap"]]})
        with pytest.raises(IncompatibleTechniqueError, match="No compatible selected techniques"):
            await scenario.get_run_size_estimate_async()
