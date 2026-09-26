# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Garak API-key scenario."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.converter import Base64Converter, Converter
from pyrit.executor.attack import PromptSendingAttack
from pyrit.models import ComponentIdentifier, ScenarioRunSizeEstimateStatus, Seed, SeedDataset
from pyrit.prompt_target import PromptTarget
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration, DatasetConstraintError
from pyrit.scenario.core.scenario import BaselineAttackPolicy
from pyrit.scenario.garak import (  # type: ignore[ty:unresolved-import]
    ApiKey,
    ApiKeyDatasetConfiguration,
    ApiKeyTechnique,
)
from pyrit.scenario.scenarios.garak.api_key import DATASET_PARTIAL_KEYS, DATASET_SAFE_PLACEHOLDERS
from pyrit.score import CredentialLeakScorer, TrueFalseScorer


@pytest.fixture
def mock_objective_target() -> MagicMock:
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = ComponentIdentifier(class_name="MockTarget", class_module="test")
    return target


@pytest.fixture
def corpus_seeds() -> dict[str, list[Seed]]:
    directory = Path(__file__).parents[4] / "pyrit" / "datasets" / "seed_datasets" / "local" / "garak"
    return {
        name: list(SeedDataset.from_yaml_file(directory / f"{name.removeprefix('garak_')}.prompt").seeds)
        for name in ApiKey.required_datasets()
    }


async def _initialize_async(
    *,
    scenario: ApiKey,
    target: PromptTarget,
    corpus_seeds: dict[str, list[Seed]],
    techniques: list[ApiKeyTechnique] | None = None,
    dataset_config: DatasetAttackConfiguration | None = None,
    technique_converters: dict[str, list[Converter]] | None = None,
) -> None:
    scenario.set_params_from_args(
        args={
            "objective_target": target,
            "scenario_techniques": techniques,
            "dataset_config": dataset_config,
            "technique_converters": technique_converters,
        }
    )
    with patch.object(
        ApiKeyDatasetConfiguration, "_collect_named_seeds_async", new_callable=AsyncMock, return_value=corpus_seeds
    ):
        await scenario.initialize_async()


def _objectives(scenario: ApiKey) -> dict[str, list[str]]:
    return {
        attack.atomic_attack_name: [group.objective.value for group in attack.seed_groups]
        for attack in scenario._atomic_attacks
    }


@pytest.mark.usefixtures("patch_central_database")
class TestApiKey:
    def test_defaults_and_standard_parameters(self) -> None:
        scenario = ApiKey()
        parameters = {parameter.name for parameter in ApiKey.supported_parameters()}

        assert scenario.name == "ApiKey"
        assert scenario.VERSION == 1
        assert scenario.BASELINE_ATTACK_POLICY is BaselineAttackPolicy.Forbidden
        assert scenario._default_dataset_config.dataset_names == ApiKey.required_datasets()
        assert scenario._default_dataset_config.max_dataset_size == 20
        assert {"dataset_config", "technique_converters"} <= parameters
        assert "prompt_cap" not in parameters
        expected = {ApiKeyTechnique.GetKey, ApiKeyTechnique.CompleteKey}
        assert set(ApiKeyTechnique.expand({ApiKeyTechnique.DEFAULT})) == expected
        assert set(ApiKeyTechnique.expand({ApiKeyTechnique.ALL})) == expected

    @pytest.mark.parametrize("technique", [ApiKeyTechnique.GetKey, ApiKeyTechnique.CompleteKey])
    async def test_single_technique_uses_entire_sample(
        self, technique: ApiKeyTechnique, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed]]
    ) -> None:
        scenario = ApiKey()
        await _initialize_async(
            scenario=scenario, target=mock_objective_target, corpus_seeds=corpus_seeds, techniques=[technique]
        )

        assert list(_objectives(scenario)) == [technique.value]
        assert len(scenario._atomic_attacks[0].seed_groups) == 20

    async def test_uncapped_configuration_renders_full_corpus(
        self, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed]]
    ) -> None:
        scenario = ApiKey()
        await _initialize_async(
            scenario=scenario,
            target=mock_objective_target,
            corpus_seeds=corpus_seeds,
            dataset_config=ApiKeyDatasetConfiguration(dataset_names=ApiKey.required_datasets()),
        )

        assert {name: len(groups) for name, groups in _objectives(scenario).items()} == {
            "get_key": 58,
            "complete_key": 290,
        }
        for attack in scenario._atomic_attacks:
            for group in attack.seed_groups:
                assert "{{" not in group.prompts[0].value
                assert group.prompts[0].source
                assert group.objective.source
                assert group.objective.metadata["technique"] == attack.atomic_attack_name

    async def test_standard_sampling_can_select_rows_beyond_the_prefix(
        self, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed]]
    ) -> None:
        scenario = ApiKey()
        with patch(
            "pyrit.scenario.core.dataset_configuration.random.sample", side_effect=lambda rows, size: rows[-size:]
        ):
            await _initialize_async(
                scenario=scenario,
                target=mock_objective_target,
                corpus_seeds=corpus_seeds,
                dataset_config=ApiKeyDatasetConfiguration(dataset_names=ApiKey.required_datasets(), max_dataset_size=3),
            )

        assert list(_objectives(scenario)) == ["complete_key"]
        groups = scenario._atomic_attacks[0].seed_groups
        assert len(groups) == 3
        assert all(group.objective.metadata["service"] == "Zoho Webhook Token" for group in groups)

    @pytest.mark.parametrize("size", [1, 20])
    async def test_resume_replays_sample_after_corpus_order_changes(
        self, size: int, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed]]
    ) -> None:
        original = ApiKey()
        config = ApiKeyDatasetConfiguration(dataset_names=ApiKey.required_datasets(), max_dataset_size=size)
        await _initialize_async(
            scenario=original, target=mock_objective_target, corpus_seeds=corpus_seeds, dataset_config=config
        )
        resumed = ApiKey(scenario_result_id=original._scenario_result_id)

        with patch("pyrit.scenario.core.dataset_configuration.random.sample", side_effect=AssertionError("resampled")):
            await _initialize_async(
                scenario=resumed,
                target=mock_objective_target,
                corpus_seeds={name: list(reversed(seeds)) for name, seeds in corpus_seeds.items()},
                dataset_config=ApiKeyDatasetConfiguration(
                    dataset_names=ApiKey.required_datasets(), max_dataset_size=size
                ),
            )

        assert _objectives(resumed) == _objectives(original)

    async def test_default_scorer_receives_all_exclusions(
        self, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed]]
    ) -> None:
        scenario = ApiKey()
        await _initialize_async(scenario=scenario, target=mock_objective_target, corpus_seeds=corpus_seeds)
        scorer = scenario._objective_scorer

        assert isinstance(scorer, CredentialLeakScorer)
        assert scorer._patterns == CredentialLeakScorer.GARAK_PATTERNS
        assert len(CredentialLeakScorer()._patterns) == 12
        assert scorer._excluded_values == frozenset(
            seed.value for name in (DATASET_PARTIAL_KEYS, DATASET_SAFE_PLACEHOLDERS) for seed in corpus_seeds[name]
        )
        assert scenario._objective_scorer_identifier == scorer.get_identifier()
        assert all(attack.attack_technique.attack._objective_scorer is scorer for attack in scenario._atomic_attacks)

    async def test_custom_scorer_and_converters_are_preserved(
        self, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed]]
    ) -> None:
        scorer = MagicMock(spec=TrueFalseScorer)
        scorer.get_identifier.return_value = ComponentIdentifier(class_name="CustomScorer", class_module="test")
        converter = Base64Converter()
        scenario = ApiKey(objective_scorer=scorer)
        await _initialize_async(
            scenario=scenario,
            target=mock_objective_target,
            corpus_seeds=corpus_seeds,
            dataset_config=ApiKeyDatasetConfiguration(dataset_names=ApiKey.required_datasets()),
            technique_converters={"get_key": [converter]},
        )

        for attack in scenario._atomic_attacks:
            strategy = attack.attack_technique.attack
            assert strategy._objective_scorer is scorer
            converters = [item for config in strategy.get_request_converters() for item in config.converters]
            assert converters == ([converter] if attack.atomic_attack_name == "get_key" else [])

    async def test_custom_validator_is_preserved(
        self, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed]]
    ) -> None:
        validator = MagicMock(side_effect=DatasetConstraintError("custom validator"))
        with pytest.raises(DatasetConstraintError, match="custom validator"):
            await _initialize_async(
                scenario=ApiKey(),
                target=mock_objective_target,
                corpus_seeds=corpus_seeds,
                dataset_config=ApiKeyDatasetConfiguration(
                    dataset_names=ApiKey.required_datasets(), validators=[validator]
                ),
            )
        validator.assert_called_once()

    @pytest.mark.parametrize(
        "config",
        [
            DatasetAttackConfiguration(dataset_names=ApiKey.required_datasets()),
            ApiKeyDatasetConfiguration(dataset_names=[DATASET_PARTIAL_KEYS]),
            ApiKeyDatasetConfiguration(seeds=[]),
        ],
    )
    async def test_unsupported_dataset_configuration_raises(
        self,
        config: DatasetAttackConfiguration,
        mock_objective_target: PromptTarget,
        corpus_seeds: dict[str, list[Seed]],
    ) -> None:
        with pytest.raises(DatasetConstraintError, match="ApiKey"):
            await _initialize_async(
                scenario=ApiKey(), target=mock_objective_target, corpus_seeds=corpus_seeds, dataset_config=config
            )

    @pytest.mark.parametrize("size", [1, 7, 20, 348, None])
    async def test_launch_and_estimate_use_standard_dataset_size(
        self, size: int | None, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed]]
    ) -> None:
        scenario = ApiKey()
        args = ScenarioConfigurationResolver.resolve_configuration(
            scenario_name="garak.api_key",
            scenario_class=ApiKey,
            objective_target=mock_objective_target,
            max_dataset_size=size,
        )
        scenario.set_params_from_args(args=args)
        with patch.object(
            ApiKeyDatasetConfiguration,
            "_collect_named_seeds_async",
            new_callable=AsyncMock,
            return_value=corpus_seeds,
        ):
            estimate = await scenario.get_run_size_estimate_async(target_is_configured=True)
            await scenario.initialize_async()

        expected = size or 20
        assert estimate.status is ScenarioRunSizeEstimateStatus.Exact
        assert estimate.total_attack_count == expected
        assert estimate.estimated_attack_count == expected
        assert estimate.minimum_attack_count == expected
        assert estimate.maximum_attack_count == expected
        assert len(estimate.dataset_cap_provenance) == 1
        assert estimate.dataset_cap_provenance[0].count == expected
        assert estimate.dataset_cap_provenance[0].dataset_name is None
        assert estimate.dataset_cap_provenance[0].dataset_names == ["get_key", "complete_key"]
        assert all(summary.effective_cap is None for summary in estimate.datasets)
        assert all(len(summary.configured_caps) == 1 for summary in estimate.datasets)
        assert all(
            [(factor.label, factor.count) for factor in component.factors]
            == [("selected synthesized requests", component.count)]
            for component in estimate.components
        )
        assert sum(len(attack.seed_groups) for attack in scenario._atomic_attacks) == expected
        assert all(
            isinstance(attack.attack_technique.attack, PromptSendingAttack) for attack in scenario._atomic_attacks
        )
        assert sum(dataset.logical_seed_group_count for dataset in estimate.datasets) == 348

        for dataset in estimate.datasets:
            assert dataset.kind == "synthesized"
            assert len(dataset.configured_caps) == 1
            cap = dataset.configured_caps[0]
            assert cap.label == "combined configuration cap"
            assert cap.count == expected
            assert cap.configured_on == "configuration"
            assert cap.dataset_name == dataset.name
            assert cap.dataset_names == ["get_key", "complete_key"]

    @pytest.mark.parametrize("size", [None, 3])
    @pytest.mark.parametrize("technique", [ApiKeyTechnique.GetKey, ApiKeyTechnique.CompleteKey])
    async def test_single_technique_estimate_preserves_cap(
        self,
        size: int | None,
        technique: ApiKeyTechnique,
        mock_objective_target: PromptTarget,
        corpus_seeds: dict[str, list[Seed]],
    ) -> None:
        scenario = ApiKey()
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "scenario_techniques": [technique],
                "dataset_config": ApiKeyDatasetConfiguration(
                    dataset_names=ApiKey.required_datasets(), max_dataset_size=size
                ),
            }
        )
        with patch.object(
            ApiKeyDatasetConfiguration, "_collect_named_seeds_async", new_callable=AsyncMock, return_value=corpus_seeds
        ):
            estimate = await scenario.get_run_size_estimate_async(target_is_configured=True)

        population_size = 58 if technique is ApiKeyTechnique.GetKey else 290
        assert estimate.estimated_attack_count == (size or population_size)
        assert len(estimate.datasets) == 1
        summary = estimate.datasets[0]
        assert summary.name == technique.value
        assert summary.logical_seed_group_count == population_size
        assert summary.selected_seed_group_count == (size or population_size)
        if size is None:
            assert summary.configured_caps == []
        else:
            assert len(summary.configured_caps) == 1
            assert summary.configured_caps[0].count == size
            assert summary.configured_caps[0].configured_on == "configuration"

    async def test_real_local_datasets_resolve_through_memory(self, mock_objective_target: PromptTarget) -> None:
        scenario = ApiKey()
        scenario.set_params_from_args(args={"objective_target": mock_objective_target})
        await scenario.initialize_async()

        assert sum(len(attack.seed_groups) for attack in scenario._atomic_attacks) == 20
