# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the Garak API-key scenario."""

from pathlib import Path
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.converter import Base64Converter, Converter
from pyrit.executor.attack import PromptSendingAttack
from pyrit.models import (
    ComponentIdentifier,
    DatasetFetchPolicy,
    ScenarioDatasetRequest,
    ScenarioRunSizeEstimateStatus,
    Seed,
    SeedDataset,
)
from pyrit.prompt_target import PromptTarget
from pyrit.scenario import DatasetSource, ScenarioRequiredData
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration, DatasetConstraintError
from pyrit.scenario.core.required_data import _RequiredDatasetConfiguration
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
def corpus_seeds() -> dict[str, list[Seed[Any]]]:
    directory = Path(__file__).parents[4] / "pyrit" / "datasets" / "seed_datasets" / "local" / "garak"
    return {
        name: list(SeedDataset.from_yaml_file(directory / f"{name.removeprefix('garak_')}.prompt").seeds)
        for name in ApiKey.required_datasets()
    }


@pytest.fixture
def garak_dataset_names(corpus_seeds: dict[str, list[Seed[Any]]]) -> list[str]:
    return list(corpus_seeds)


async def _initialize_async(
    *,
    scenario: ApiKey,
    target: PromptTarget,
    corpus_seeds: dict[str, list[Seed[Any]]],
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
    with (
        patch.object(ScenarioRequiredData, "prepare_async", new_callable=AsyncMock),
        patch.object(
            _RequiredDatasetConfiguration,
            "_collect_named_seeds_async",
            new_callable=AsyncMock,
            return_value=corpus_seeds,
        ),
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
        assert scenario._default_dataset_config.dataset_names == []
        assert scenario._default_dataset_config.max_total == 20
        assert scenario.get_required_data().dataset_names == ApiKey.required_datasets()
        assert scenario.get_default_dataset_names() == ApiKey.required_datasets()
        assert {"dataset_config", "technique_converters"} <= parameters
        assert "prompt_cap" not in parameters
        expected = {ApiKeyTechnique.GetKey, ApiKeyTechnique.CompleteKey}
        assert set(ApiKeyTechnique.expand({ApiKeyTechnique.DEFAULT})) == expected
        assert set(ApiKeyTechnique.expand({ApiKeyTechnique.ALL})) == expected

    @pytest.mark.parametrize("technique", [ApiKeyTechnique.GetKey, ApiKeyTechnique.CompleteKey])
    async def test_single_technique_uses_entire_sample(
        self, technique: ApiKeyTechnique, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed[Any]]]
    ) -> None:
        scenario = ApiKey()
        await _initialize_async(
            scenario=scenario, target=mock_objective_target, corpus_seeds=corpus_seeds, techniques=[technique]
        )

        assert list(_objectives(scenario)) == [technique.value]
        assert len(scenario._atomic_attacks[0].seed_groups) == 20

    async def test_uncapped_configuration_renders_full_corpus(
        self, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed[Any]]]
    ) -> None:
        scenario = ApiKey()
        await _initialize_async(
            scenario=scenario,
            target=mock_objective_target,
            corpus_seeds=corpus_seeds,
            dataset_config=ApiKeyDatasetConfiguration(max_total="all"),
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
        self, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed[Any]]]
    ) -> None:
        scenario = ApiKey()
        with patch(
            "pyrit.scenario.core.dataset_configuration.random.sample", side_effect=lambda rows, size: rows[-size:]
        ):
            await _initialize_async(
                scenario=scenario,
                target=mock_objective_target,
                corpus_seeds=corpus_seeds,
                dataset_config=ApiKeyDatasetConfiguration(max_total=3),
            )

        assert list(_objectives(scenario)) == ["complete_key"]
        groups = scenario._atomic_attacks[0].seed_groups
        assert len(groups) == 3
        assert all(group.objective.metadata["service"] == "Zoho Webhook Token" for group in groups)

    @pytest.mark.parametrize("size", [1, 20])
    async def test_resume_replays_sample_after_corpus_order_changes(
        self, size: int, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed[Any]]]
    ) -> None:
        original = ApiKey()
        config = ApiKeyDatasetConfiguration(max_total=size)
        await _initialize_async(
            scenario=original, target=mock_objective_target, corpus_seeds=corpus_seeds, dataset_config=config
        )
        resumed = ApiKey(scenario_result_id=original._scenario_result_id)

        with patch("pyrit.scenario.core.dataset_configuration.random.sample", side_effect=AssertionError("resampled")):
            await _initialize_async(
                scenario=resumed,
                target=mock_objective_target,
                corpus_seeds={name: list(reversed(seeds)) for name, seeds in corpus_seeds.items()},
                dataset_config=ApiKeyDatasetConfiguration(max_total=size),
            )

        assert _objectives(resumed) == _objectives(original)

    async def test_default_scorer_receives_all_exclusions(
        self, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed[Any]]]
    ) -> None:
        scenario = ApiKey()
        await _initialize_async(scenario=scenario, target=mock_objective_target, corpus_seeds=corpus_seeds)
        scorer = scenario._objective_scorer

        assert isinstance(scorer, CredentialLeakScorer)
        assert scorer._patterns == CredentialLeakScorer.GARAK_PATTERNS
        assert len(CredentialLeakScorer()._patterns) == 18
        assert scorer._excluded_values == frozenset(
            seed.value for name in (DATASET_PARTIAL_KEYS, DATASET_SAFE_PLACEHOLDERS) for seed in corpus_seeds[name]
        )
        assert scenario._objective_scorer_identifier == scorer.get_identifier()
        assert all(attack.attack_technique.attack._objective_scorer is scorer for attack in scenario._atomic_attacks)

    async def test_custom_scorer_and_converters_are_preserved(
        self, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed[Any]]]
    ) -> None:
        scorer = MagicMock(spec=TrueFalseScorer)
        scorer.get_identifier.return_value = ComponentIdentifier(class_name="CustomScorer", class_module="test")
        converter = Base64Converter()
        scenario = ApiKey(objective_scorer=scorer)
        await _initialize_async(
            scenario=scenario,
            target=mock_objective_target,
            corpus_seeds=corpus_seeds,
            dataset_config=ApiKeyDatasetConfiguration(),
            technique_converters={"get_key": [converter]},
        )

        for attack in scenario._atomic_attacks:
            strategy = attack.attack_technique.attack
            assert strategy._objective_scorer is scorer
            converters = [item for config in strategy.get_request_converters() for item in config.converters]
            assert converters == ([converter] if attack.atomic_attack_name == "get_key" else [])

    async def test_custom_validator_is_preserved(
        self, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed[Any]]]
    ) -> None:
        validator = MagicMock(side_effect=DatasetConstraintError("custom validator"))
        with pytest.raises(DatasetConstraintError, match="custom validator"):
            await _initialize_async(
                scenario=ApiKey(),
                target=mock_objective_target,
                corpus_seeds=corpus_seeds,
                dataset_config=ApiKeyDatasetConfiguration(validators=[validator]),
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
        corpus_seeds: dict[str, list[Seed[Any]]],
    ) -> None:
        with pytest.raises(DatasetConstraintError, match="ApiKey"):
            await _initialize_async(
                scenario=ApiKey(), target=mock_objective_target, corpus_seeds=corpus_seeds, dataset_config=config
            )

    @pytest.mark.parametrize("size", [1, 7, 20, 348, None, "all"])
    @pytest.mark.usefixtures("mock_dataset_preparation")
    async def test_launch_and_estimate_use_standard_dataset_size(
        self,
        size: int | Literal["all"] | None,
        mock_objective_target: PromptTarget,
        corpus_seeds: dict[str, list[Seed[Any]]],
    ) -> None:
        scenario = ApiKey()
        args = ScenarioConfigurationResolver.resolve_configuration(
            scenario_name="garak.api_key",
            scenario_class=ApiKey,
            objective_target=mock_objective_target,
            datasets=ScenarioDatasetRequest(max_total=size),
        )
        scenario.set_params_from_args(args=args)
        with patch.object(
            _RequiredDatasetConfiguration,
            "_collect_named_seeds_async",
            new_callable=AsyncMock,
            return_value=corpus_seeds,
        ):
            estimate = await scenario.get_run_size_estimate_async(target_is_configured=True)
            await scenario.initialize_async()

        total = None if size == "all" else size or 20
        expected = 348 if total is None else total
        assert estimate.status is (
            ScenarioRunSizeEstimateStatus.Approximate
            if total is not None
            else ScenarioRunSizeEstimateStatus.Unavailable
        )
        assert estimate.total_attack_count == total
        assert estimate.estimated_attack_count == total
        assert estimate.minimum_attack_count is None
        assert estimate.maximum_attack_count is None
        assert all(
            [(factor.label, factor.count) for factor in component.factors]
            == [("selected request estimate", component.count)]
            for component in estimate.components
        )
        assert sum(len(attack.seed_groups) for attack in scenario._atomic_attacks) == expected
        assert all(
            isinstance(attack.attack_technique.attack, PromptSendingAttack) for attack in scenario._atomic_attacks
        )
        assert all(dataset.logical_seed_group_count is None for dataset in estimate.datasets)

        for dataset in estimate.datasets:
            assert dataset.kind == "synthesized"
            assert [(cap.label, cap.count, cap.configured_on) for cap in dataset.configured_caps] == (
                [("combined configuration cap", total, "configuration")] if total is not None else []
            )
            assert all(cap.dataset_name == dataset.name for cap in dataset.configured_caps)

    @pytest.mark.parametrize("size", [None, 3])
    @pytest.mark.parametrize("technique", [ApiKeyTechnique.GetKey, ApiKeyTechnique.CompleteKey])
    async def test_single_technique_estimate_preserves_cap(
        self,
        size: int | None,
        technique: ApiKeyTechnique,
        mock_objective_target: PromptTarget,
        corpus_seeds: dict[str, list[Seed[Any]]],
    ) -> None:
        scenario = ApiKey()
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "scenario_techniques": [technique],
                "dataset_config": ApiKeyDatasetConfiguration(max_total=size),
            }
        )
        with patch.object(
            _RequiredDatasetConfiguration,
            "_collect_named_seeds_async",
            new_callable=AsyncMock,
            return_value=corpus_seeds,
        ):
            estimate = await scenario.get_run_size_estimate_async(target_is_configured=True)

        assert estimate.estimated_attack_count == size
        if size is None:
            assert estimate.status is ScenarioRunSizeEstimateStatus.Unavailable
            assert "No size limit" in estimate.note
            return
        assert len(estimate.datasets) == 1
        summary = estimate.datasets[0]
        assert summary.name == technique.value
        assert summary.logical_seed_group_count is None
        assert summary.selected_seed_group_count is None
        assert len(summary.configured_caps) == 1
        assert summary.configured_caps[0].count == size
        assert summary.configured_caps[0].configured_on == "configuration"

    @pytest.mark.usefixtures("mock_garak_dataset_fetch")
    async def test_real_local_datasets_resolve_through_memory(self, mock_objective_target: PromptTarget) -> None:
        scenario = ApiKey()
        scenario.set_params_from_args(args={"objective_target": mock_objective_target})
        await scenario.initialize_async()

        assert sum(len(attack.seed_groups) for attack in scenario._atomic_attacks) == 20

    def test_corpus_is_required_data_and_the_configuration_only_limits_requests(self) -> None:
        scenario = ApiKey()
        settings = scenario.get_default_dataset_settings()

        assert scenario._default_dataset_config.sources == ()
        assert [(source.name, source.max_size) for source in settings.sources] == [
            (name, "all") for name in ApiKey.required_datasets()
        ]
        assert settings.max_total == 20

    async def test_only_a_fresh_run_prepares_the_corpus_async(
        self, *, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed[Any]]]
    ) -> None:
        scenario = ApiKey()
        with (
            patch.object(ScenarioRequiredData, "prepare_async", new_callable=AsyncMock) as prepare,
            patch.object(
                _RequiredDatasetConfiguration,
                "_collect_named_seeds_async",
                new_callable=AsyncMock,
                return_value=corpus_seeds,
            ),
        ):
            scenario.set_params_from_args(args={"objective_target": mock_objective_target})
            await scenario.get_run_size_estimate_async(target_is_configured=True)
            prepare.assert_not_awaited()

            await scenario.initialize_async()
            prepare.assert_awaited_once()

            resumed = ApiKey(scenario_result_id=scenario._scenario_result_id)
            resumed.set_params_from_args(args={"objective_target": mock_objective_target})
            await resumed.initialize_async()
            prepare.assert_awaited_once()

        assert _objectives(resumed) == _objectives(scenario)

    async def test_estimate_never_reads_the_corpus_async(self, *, mock_objective_target: PromptTarget) -> None:
        scenario = ApiKey()
        scenario.set_params_from_args(args={"objective_target": mock_objective_target})
        with patch.object(ScenarioRequiredData, "load_async", side_effect=AssertionError("read corpus")):
            estimate = await scenario.get_run_size_estimate_async(target_is_configured=True)

        assert estimate.estimated_attack_count == 20

    async def test_refresh_reaches_the_corpus_async(self, *, mock_objective_target: PromptTarget) -> None:
        scenario = ApiKey()
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "dataset_overrides": {
                    "fetch": "always",
                    "sources": [
                        {"name": name, "fetch": "never" if name == DATASET_PARTIAL_KEYS else None}
                        for name in ApiKey.required_datasets()
                    ],
                },
            }
        )
        estimate = await scenario.get_run_size_estimate_async(target_is_configured=True)

        assert [(source.name, source.fetch) for source in estimate.effective_datasets.sources] == [
            (name, DatasetFetchPolicy.NEVER if name == DATASET_PARTIAL_KEYS else DatasetFetchPolicy.ALWAYS)
            for name in ApiKey.required_datasets()
        ]

    async def test_corpus_rows_cannot_be_capped_through_overrides_async(
        self, *, mock_objective_target: PromptTarget
    ) -> None:
        scenario = ApiKey()
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "dataset_overrides": {
                    "sources": [{"name": name, "max_size": 2} for name in ApiKey.required_datasets()]
                },
            }
        )
        with pytest.raises(DatasetConstraintError, match="can't be capped"):
            await scenario.get_run_size_estimate_async(target_is_configured=True)

    async def test_identity_still_lists_the_corpus_async(
        self, *, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed[Any]]]
    ) -> None:
        scenario = ApiKey()
        await _initialize_async(scenario=scenario, target=mock_objective_target, corpus_seeds=corpus_seeds)

        assert scenario._build_scenario_identifier().datasets == ApiKey.required_datasets()

    async def test_listing_the_corpus_as_sources_still_works_with_a_warning_async(
        self, *, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed[Any]]]
    ) -> None:
        current = ApiKey()
        await _initialize_async(
            scenario=current,
            target=mock_objective_target,
            corpus_seeds=corpus_seeds,
            dataset_config=ApiKeyDatasetConfiguration(max_total="all"),
        )
        legacy = ApiKey()
        with pytest.warns(DeprecationWarning, match="ApiKeyDatasetConfiguration"):
            await _initialize_async(
                scenario=legacy,
                target=mock_objective_target,
                corpus_seeds=corpus_seeds,
                dataset_config=ApiKeyDatasetConfiguration(
                    sources=[
                        DatasetSource(name=name, fetch=DatasetFetchPolicy.IF_MISSING)
                        for name in ApiKey.required_datasets()
                    ],
                    max_total="all",
                ),
            )

        assert _objectives(legacy) == _objectives(current)
        assert legacy._dataset_config.sources == ()
        assert legacy._required_data is not None
        assert [source.fetch for source in legacy._required_data.sources] == [DatasetFetchPolicy.IF_MISSING] * 4
        assert legacy._build_scenario_identifier().datasets == current._build_scenario_identifier().datasets

    async def test_capped_corpus_sources_still_fail_async(self, *, mock_objective_target: PromptTarget) -> None:
        scenario = ApiKey()
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "dataset_config": ApiKeyDatasetConfiguration(
                    sources=[DatasetSource(name=name) for name in ApiKey.required_datasets()], max_per_dataset=2
                ),
            }
        )
        with pytest.warns(DeprecationWarning), pytest.raises(DatasetConstraintError, match="can't be capped"):
            await scenario.get_run_size_estimate_async(target_is_configured=True)

    async def test_missing_template_fails_before_assembly_async(
        self, *, mock_objective_target: PromptTarget, corpus_seeds: dict[str, list[Seed[Any]]]
    ) -> None:
        corpus = {**corpus_seeds, "garak_api_key_templates": corpus_seeds["garak_api_key_templates"][:1]}
        with pytest.raises(DatasetConstraintError, match="one prompt template"):
            await _initialize_async(scenario=ApiKey(), target=mock_objective_target, corpus_seeds=corpus)

    async def test_listed_corpus_sources_still_take_overrides_async(
        self, *, mock_objective_target: PromptTarget
    ) -> None:
        scenario = ApiKey()
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "dataset_config": ApiKeyDatasetConfiguration(
                    sources=[DatasetSource(name=name) for name in ApiKey.required_datasets()], max_total=5
                ),
                "dataset_overrides": {
                    "max_total": 3,
                    "sources": [
                        {"name": name, "fetch": "always" if name == DATASET_PARTIAL_KEYS else None}
                        for name in ApiKey.required_datasets()
                    ],
                },
            }
        )
        with pytest.warns(DeprecationWarning, match="ApiKeyDatasetConfiguration"):
            estimate = await scenario.get_run_size_estimate_async(target_is_configured=True)

        assert estimate.estimated_attack_count == 3
        assert [(source.name, source.fetch) for source in estimate.effective_datasets.sources] == [
            (name, DatasetFetchPolicy.ALWAYS if name == DATASET_PARTIAL_KEYS else DatasetFetchPolicy.IF_MISSING)
            for name in ApiKey.required_datasets()
        ]

    @pytest.mark.usefixtures("mock_garak_dataset_fetch")
    @pytest.mark.parametrize(
        ("category", "error"), [("cyber", None), ("violence", "none match the configured filters")]
    )
    async def test_dataset_filters_reach_the_corpus_async(
        self, *, category: str, error: str | None, mock_objective_target: PromptTarget
    ) -> None:
        scenario = ApiKey()
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "dataset_overrides": {"filters": {"harm_categories": [category]}},
            }
        )

        if error:
            with pytest.raises(DatasetConstraintError, match=error):
                await scenario.initialize_async()
            return
        await scenario.initialize_async()
        assert sum(len(attack.seed_groups) for attack in scenario._atomic_attacks) == 20

    async def test_configuration_fetch_and_filters_reach_the_corpus_async(
        self, *, mock_objective_target: PromptTarget
    ) -> None:
        scenario = ApiKey()
        scenario.set_params_from_args(
            args={
                "objective_target": mock_objective_target,
                "dataset_config": ApiKeyDatasetConfiguration(
                    fetch=DatasetFetchPolicy.NEVER, filters={"harm_categories": ["cyber"]}
                ),
            }
        )
        await scenario.get_run_size_estimate_async(target_is_configured=True)

        assert scenario._required_data is not None
        assert scenario._required_data.fetch is DatasetFetchPolicy.NEVER
        assert scenario._required_data.filters == {"harm_categories": ["cyber"]}
        with pytest.raises(DatasetConstraintError, match="fetch is 'never'"):
            await scenario.initialize_async()
