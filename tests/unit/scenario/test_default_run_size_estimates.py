# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for scenario-owned default-run size estimates."""

from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.models import (
    AttackSeedGroup,
    AttackTechniqueSeedGroup,
    ComponentIdentifier,
    ScenarioDatasetSizeCap,
    ScenarioDatasetSummary,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
)
from pyrit.prompt_target import PromptTarget
from pyrit.scenario.core import (
    AtomicAttack,
    BaselineAttackPolicy,
    DatasetAttackConfiguration,
    DatasetConfiguration,
    Scenario,
    ScenarioTechnique,
)
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.scenario.scenarios.adaptive.text_adaptive import TextAdaptive
from pyrit.scenario.scenarios.airt.jailbreak import Jailbreak
from pyrit.scenario.scenarios.airt.psychosocial import Psychosocial
from pyrit.scenario.scenarios.benchmark.adversarial import AdversarialBenchmark
from pyrit.scenario.scenarios.foundry.red_team_agent import FoundryComposite, FoundryTechnique, RedTeamAgent
from pyrit.scenario.scenarios.garak.encoding import Encoding
from pyrit.scenario.scenarios.garak.web_injection import WebInjection
from pyrit.score import TrueFalseScorer


class _TwoTechniqueDefault(ScenarioTechnique):
    """Two concrete defaults used by estimate-only test scenarios."""

    ALL = ("all", {"all"})
    DEFAULT = ("default", {"default"})
    ONE = ("one", {"default"})
    TWO = ("two", {"default"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        """Return aggregate tags."""
        return {"all", "default"}

    @classmethod
    def default(cls) -> "_TwoTechniqueDefault":
        """Return the default aggregate."""
        return cls.DEFAULT


class _JailbreakDefault(ScenarioTechnique):
    """Jailbreak's two default delivery techniques."""

    ALL = ("all", {"all"})
    DEFAULT = ("default", {"default"})
    PROMPT_SENDING = ("prompt_sending", {"default"})
    SYSTEM_PROMPT = ("jailbreak_system_prompt", {"default"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        """Return aggregate tags."""
        return {"all", "default"}

    @classmethod
    def default(cls) -> "_JailbreakDefault":
        """Return the default aggregate."""
        return cls.DEFAULT


class _MatrixEstimateScenario(Scenario):
    """Minimal ordinary default technique sweep."""

    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Enabled

    def __init__(self, *, objective_scorer: TrueFalseScorer) -> None:
        super().__init__(
            version=1,
            technique_class=_TwoTechniqueDefault,
            default_dataset_config=DatasetAttackConfiguration(dataset_names=["sample"]),
            objective_scorer=objective_scorer,
        )

    async def _resolve_seed_groups_by_dataset_async(
        self, *, apply_sampling: bool = True
    ) -> dict[str, list[AttackSeedGroup]]:
        """Return three logical groups before selection and two after."""
        if self._dataset_config.dataset_names == ["sample"]:
            values = ["one", "two"] if apply_sampling else ["one", "two", "three"]
            return {"sample": [_seed_group(value) for value in values]}
        return await super()._resolve_seed_groups_by_dataset_async(apply_sampling=apply_sampling)

    async def _build_atomic_attacks_async(self, *, context):
        """Return no attacks; only estimation is exercised."""
        return []


class _CompatibilityMatrixEstimateScenario(_MatrixEstimateScenario):
    """Matrix scenario whose estimates mirror execution compatibility filtering."""

    RUN_SIZE_USES_FACTORY_COMPATIBILITY: ClassVar[bool] = True


class _NamedDatasetEstimateScenario(Scenario):
    """Minimal estimate scenario that resolves a named dataset."""

    def __init__(self, *, objective_scorer: TrueFalseScorer) -> None:
        super().__init__(
            version=1,
            technique_class=_TwoTechniqueDefault,
            default_dataset_config=DatasetAttackConfiguration(dataset_names=["sample"]),
            objective_scorer=objective_scorer,
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """Return no attacks; only estimation is exercised."""
        return []


def _scorer() -> MagicMock:
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.get_identifier.return_value = ComponentIdentifier(class_name="MockScorer", class_module="test")
    return scorer


def _seed_group(value: str) -> AttackSeedGroup:
    return AttackSeedGroup(seeds=[SeedObjective(value=value)])


def _resolved_groups(
    counts: dict[str, int],
) -> tuple[dict[str, list[AttackSeedGroup]], list[ScenarioDatasetSummary]]:
    groups = {name: [_seed_group(f"{name}-{index}") for index in range(count)] for name, count in counts.items()}
    summaries = [
        ScenarioDatasetSummary(
            name=name,
            logical_seed_group_count=count,
            selected_seed_group_count=count,
        )
        for name, count in counts.items()
    ]
    return groups, summaries


@pytest.mark.usefixtures("patch_central_database")
async def test_ordinary_matrix_estimate_uses_planned_seed_units_and_baseline() -> None:
    """The base estimate is selected seed groups times concrete defaults plus baseline."""
    estimate = await _MatrixEstimateScenario(objective_scorer=_scorer()).get_default_run_size_estimate_async()
    assert estimate.estimated_attack_count == 6
    assert [component.count for component in estimate.components] == [4, 2]
    assert estimate.datasets[0].logical_seed_group_count == 3
    assert estimate.datasets[0].selected_seed_group_count == 2


@pytest.mark.usefixtures("patch_central_database")
async def test_configured_estimate_reuses_technique_and_baseline_resolution_without_persistence(
    patch_central_database,
) -> None:
    """A configured estimate expands only selected inputs and creates no ScenarioResult."""
    scenario = _MatrixEstimateScenario(objective_scorer=_scorer())
    scenario.set_params_from_args(
        args={
            "scenario_techniques": [_TwoTechniqueDefault.ONE],
            "include_baseline": False,
        }
    )

    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.estimated_attack_count == 2
    assert [component.count for component in estimate.components] == [2]
    assert patch_central_database.return_value.get_scenario_results() == []


async def test_configured_estimate_auto_fetches_selected_named_dataset() -> None:
    """A configured estimate loads its selected dataset through DatasetConfiguration."""
    memory = MagicMock()
    memory.get_seeds.return_value = []

    async def populate_memory_async(*, dataset_name: str) -> None:
        assert dataset_name == "sample"
        memory.get_seeds.return_value = [
            SeedObjective(value="one", dataset_name="sample"),
            SeedObjective(value="two", dataset_name="sample"),
        ]

    with (
        patch(
            "pyrit.scenario.core.dataset_configuration.CentralMemory.get_memory_instance",
            return_value=memory,
        ),
        patch.object(
            DatasetConfiguration,
            "_fetch_dataset_async",
            new_callable=AsyncMock,
            side_effect=populate_memory_async,
        ) as fetch_dataset,
    ):
        scenario = _NamedDatasetEstimateScenario(objective_scorer=_scorer())
        scenario.set_params_from_args(args={})
        estimate = await scenario.get_run_size_estimate_async()

    assert estimate.estimated_attack_count == 6
    fetch_dataset.assert_awaited_once_with(dataset_name="sample")


@pytest.mark.usefixtures("patch_central_database")
async def test_configured_estimate_expands_requested_aggregate() -> None:
    """Configured previews expand aggregate technique tokens through the scenario path."""
    scenario = _MatrixEstimateScenario(objective_scorer=_scorer())
    scenario.set_params_from_args(
        args={
            "scenario_techniques": [_TwoTechniqueDefault.DEFAULT],
            "include_baseline": False,
        }
    )

    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.estimated_attack_count == 4


@pytest.mark.usefixtures("patch_central_database")
async def test_configured_estimate_applies_dataset_selection_and_cap() -> None:
    """Configured estimates use the requested dataset population rather than scenario defaults."""
    scenario = _MatrixEstimateScenario(objective_scorer=_scorer())
    scenario.set_params_from_args(
        args={
            "dataset_config": DatasetAttackConfiguration(
                seed_groups=[_seed_group("one"), _seed_group("two"), _seed_group("three")],
                max_dataset_size=2,
            ),
            "scenario_techniques": [_TwoTechniqueDefault.ONE],
            "include_baseline": False,
        }
    )

    estimate = await scenario.get_run_size_estimate_async()

    assert estimate.estimated_attack_count == 2
    assert len(estimate.datasets) == 1
    assert estimate.datasets[0].logical_seed_group_count == 3
    assert estimate.datasets[0].selected_seed_group_count == 2


@pytest.mark.usefixtures("patch_central_database")
async def test_configured_estimate_exposes_nonbinding_cap_provenance() -> None:
    """Configured caps remain visible even when they do not reduce the population."""
    scenario = _MatrixEstimateScenario(objective_scorer=_scorer())
    scenario.set_params_from_args(
        args={
            "dataset_config": DatasetAttackConfiguration(
                seed_groups=[_seed_group(str(index)) for index in range(4)],
                max_dataset_size=4,
            ),
            "scenario_techniques": [_TwoTechniqueDefault.ONE],
            "include_baseline": False,
        }
    )

    estimate = await scenario.get_run_size_estimate_async()

    assert estimate.datasets[0].logical_seed_group_count == 4
    assert estimate.datasets[0].selected_seed_group_count == 4
    assert [(cap.label, cap.count, cap.configured_on) for cap in estimate.datasets[0].configured_caps] == [
        ("per-dataset cap", 4, "dataset")
    ]


@pytest.mark.usefixtures("patch_central_database")
async def test_matrix_estimate_filters_each_technique_seed_population_like_execution() -> None:
    """A mixed seed matrix does not use naive technique-by-group multiplication."""
    compatible = _seed_group("compatible")
    incompatible = AttackSeedGroup(
        seeds=[
            SeedObjective(value="incompatible"),
            SeedPrompt(value="user", data_type="text", role="user", sequence=0),
            SeedPrompt(value="assistant", data_type="text", role="assistant", sequence=1),
            SeedPrompt(value="user again", data_type="text", role="user", sequence=2),
        ]
    )
    plain_factory = MagicMock()
    plain_factory.seed_technique = None
    conversation_factory = MagicMock()
    conversation_factory.seed_technique = AttackTechniqueSeedGroup(
        seeds=[
            SeedSimulatedConversation(
                adversarial_chat_system_prompt_path="fake.yaml",
                num_turns=3,
            )
        ]
    )
    scenario = _CompatibilityMatrixEstimateScenario(objective_scorer=_scorer())
    scenario.set_params_from_args(args={"include_baseline": False})
    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(
        return_value=(
            {"sample": [compatible, incompatible]},
            [
                ScenarioDatasetSummary(
                    name="sample",
                    logical_seed_group_count=2,
                    selected_seed_group_count=2,
                )
            ],
        )
    )

    with patch(
        "pyrit.scenario.core.matrix_atomic_attack_builder.resolve_technique_factories_for_techniques",
        return_value={"one": plain_factory, "two": conversation_factory},
    ):
        estimate = await scenario.get_run_size_estimate_async()

    assert estimate.estimated_attack_count == 3
    assert [(component.label, component.count) for component in estimate.components] == [("one", 2), ("two", 1)]


@pytest.mark.usefixtures("patch_central_database")
async def test_matrix_estimate_with_binding_cap_is_exact_when_every_group_is_compatible() -> None:
    """A randomized binding cap is exact when every possible sample has the same size."""
    scenario = _CompatibilityMatrixEstimateScenario(objective_scorer=_scorer())
    scenario.set_params_from_args(args={"include_baseline": False})
    full_groups = [_seed_group("one"), _seed_group("two")]

    async def resolve_groups() -> tuple[dict[str, list[AttackSeedGroup]], list[ScenarioDatasetSummary]]:
        scenario._estimate_has_binding_size_cap = True
        scenario._estimate_full_groups_by_dataset = {"sample": full_groups}
        return (
            {"sample": full_groups[:1]},
            [
                ScenarioDatasetSummary(
                    name="sample",
                    logical_seed_group_count=2,
                    selected_seed_group_count=1,
                    configured_caps=[
                        ScenarioDatasetSizeCap(
                            label="per-dataset cap",
                            count=1,
                            configured_on="dataset",
                            dataset_name="sample",
                        )
                    ],
                )
            ],
        )

    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(side_effect=resolve_groups)
    factory = MagicMock()
    factory.seed_technique = None

    with patch(
        "pyrit.scenario.core.matrix_atomic_attack_builder.resolve_technique_factories_for_techniques",
        return_value={"one": factory, "two": factory},
    ):
        estimate = await scenario.get_run_size_estimate_async()
    assert estimate.estimated_attack_count == 2
    assert estimate.minimum_attack_count is None
    assert estimate.maximum_attack_count is None


@pytest.mark.usefixtures("patch_central_database")
async def test_matrix_estimate_with_binding_cap_reports_compatibility_bounds() -> None:
    """A randomized binding cap reports bounds when compatible sample counts can differ."""
    scenario = _CompatibilityMatrixEstimateScenario(objective_scorer=_scorer())
    scenario.set_params_from_args(args={"include_baseline": False})
    compatible = _seed_group("compatible")
    incompatible = AttackSeedGroup(
        seeds=[
            SeedObjective(value="incompatible"),
            SeedPrompt(value="user", data_type="text", role="user", sequence=0),
            SeedPrompt(value="assistant", data_type="text", role="assistant", sequence=1),
            SeedPrompt(value="user again", data_type="text", role="user", sequence=2),
        ]
    )

    async def resolve_groups() -> tuple[dict[str, list[AttackSeedGroup]], list[ScenarioDatasetSummary]]:
        scenario._estimate_has_binding_size_cap = True
        scenario._estimate_full_groups_by_dataset = {"sample": [compatible, incompatible]}
        return (
            {"sample": [compatible]},
            [
                ScenarioDatasetSummary(
                    name="sample",
                    logical_seed_group_count=2,
                    selected_seed_group_count=1,
                    configured_caps=[
                        ScenarioDatasetSizeCap(
                            label="per-dataset cap",
                            count=1,
                            configured_on="dataset",
                            dataset_name="sample",
                        )
                    ],
                )
            ],
        )

    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(side_effect=resolve_groups)
    plain_factory = MagicMock()
    plain_factory.seed_technique = None
    conversation_factory = MagicMock()
    conversation_factory.seed_technique = AttackTechniqueSeedGroup(
        seeds=[
            SeedSimulatedConversation(
                adversarial_chat_system_prompt_path="fake.yaml",
                num_turns=3,
            )
        ]
    )

    with patch(
        "pyrit.scenario.core.matrix_atomic_attack_builder.resolve_technique_factories_for_techniques",
        return_value={"one": plain_factory, "two": conversation_factory},
    ):
        estimate = await scenario.get_run_size_estimate_async()

    assert estimate.estimated_attack_count is None
    assert estimate.minimum_attack_count == 1
    assert estimate.maximum_attack_count == 2
    assert "range covers every compatibility mix" in estimate.note


@pytest.mark.usefixtures("patch_central_database")
async def test_matrix_estimate_with_unsupported_binding_cap_is_conditional() -> None:
    """A cross-dataset cap cannot provide independent per-dataset compatibility bounds."""
    scenario = _CompatibilityMatrixEstimateScenario(objective_scorer=_scorer())
    scenario.set_params_from_args(args={"include_baseline": False})
    full_groups = [_seed_group("one"), _seed_group("two")]

    async def resolve_groups() -> tuple[dict[str, list[AttackSeedGroup]], list[ScenarioDatasetSummary]]:
        scenario._estimate_has_binding_size_cap = True
        scenario._estimate_full_groups_by_dataset = {"sample": full_groups}
        return (
            {"sample": full_groups[:1]},
            [
                ScenarioDatasetSummary(
                    name="sample",
                    logical_seed_group_count=2,
                    selected_seed_group_count=1,
                    configured_caps=[
                        ScenarioDatasetSizeCap(
                            label="combined cap",
                            count=1,
                            configured_on="compound",
                        )
                    ],
                )
            ],
        )

    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(side_effect=resolve_groups)
    factory = MagicMock()
    factory.seed_technique = None

    with patch(
        "pyrit.scenario.core.matrix_atomic_attack_builder.resolve_technique_factories_for_techniques",
        return_value={"one": factory, "two": factory},
    ):
        estimate = await scenario.get_run_size_estimate_async()

    assert estimate.estimated_attack_count is None
    assert estimate.minimum_attack_count is None
    assert estimate.maximum_attack_count is None
    assert "binding randomized dataset cap" in estimate.note


@pytest.mark.usefixtures("patch_central_database")
def test_compatibility_bounds_skip_missing_factories_and_require_dataset_summaries() -> None:
    """Compatibility bounds ignore missing factories and reject incomplete dataset metadata."""
    scenario = _CompatibilityMatrixEstimateScenario(objective_scorer=_scorer())
    scenario._scenario_techniques = [_TwoTechniqueDefault.ONE]
    scenario._estimate_full_groups_by_dataset = {"sample": [_seed_group("one")]}

    with patch(
        "pyrit.scenario.core.matrix_atomic_attack_builder.resolve_technique_factories_for_techniques",
        return_value={},
    ):
        assert scenario._get_technique_compatibility_bounds(datasets=[]) == {}

    factory = MagicMock()
    factory.seed_technique = None
    with patch(
        "pyrit.scenario.core.matrix_atomic_attack_builder.resolve_technique_factories_for_techniques",
        return_value={"one": factory},
    ):
        assert scenario._get_technique_compatibility_bounds(datasets=[]) is None


def test_sampled_compatibility_bounds_are_exact_without_sampling() -> None:
    """An uncut population has one exact compatible-group count."""
    assert Scenario._get_sampled_compatibility_bounds(
        full_count=3,
        selected_count=3,
        compatible_count=2,
        uses_only_per_dataset_caps=False,
    ) == (2, 2)


@pytest.mark.usefixtures("patch_central_database")
async def test_adaptive_estimate_is_target_conditional_and_does_not_multiply_techniques() -> None:
    """Adaptive techniques are selected internally rather than forming an outer axis."""
    with patch.object(TextAdaptive, "get_technique_class", return_value=_TwoTechniqueDefault):
        scenario = TextAdaptive(objective_scorer=_scorer())
    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(return_value=_resolved_groups({"adaptive": 3}))

    estimate = await scenario.get_default_run_size_estimate_async()
    assert estimate.estimated_attack_count is None
    assert [component.count for component in estimate.components] == [3, 3]


@pytest.mark.usefixtures("patch_central_database")
async def test_adaptive_estimate_counts_exact_compatible_outer_envelopes_with_target() -> None:
    """A concrete target makes the compatible outer population exact without counting attempts."""
    with patch.object(TextAdaptive, "get_technique_class", return_value=_TwoTechniqueDefault):
        scenario = TextAdaptive(objective_scorer=_scorer())
    target = MagicMock(spec=PromptTarget)
    scenario.set_params_from_args(
        args={
            "objective_target": target,
            "include_baseline": False,
            "max_attempts_per_objective": 7,
        }
    )
    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(return_value=_resolved_groups({"adaptive": 3}))
    dispatcher = MagicMock()
    dispatcher.compatible_techniques.side_effect = [["one"], [], ["two"]]

    with (
        patch.object(scenario, "_build_techniques_dict", return_value={"one": MagicMock()}),
        patch(
            "pyrit.scenario.scenarios.adaptive.adaptive_scenario.AdaptiveTechniqueDispatcher",
            return_value=dispatcher,
        ),
    ):
        estimate = await scenario.get_run_size_estimate_async()
    assert estimate.estimated_attack_count == 2
    assert [component.count for component in estimate.components] == [2]
    assert "7 selected technique attempts" in estimate.note

    scenario.set_params_from_args(args={"include_baseline": False})
    estimate_without_target = await scenario.get_run_size_estimate_async()
    assert estimate_without_target.estimated_attack_count is None


@pytest.mark.usefixtures("patch_central_database")
async def test_jailbreak_estimate_exposes_template_attempt_and_target_capability_axes() -> None:
    """Jailbreak reports guaranteed inline work separately from conditional system delivery."""
    with patch("pyrit.scenario.scenarios.airt.jailbreak._build_jailbreak_technique", return_value=_JailbreakDefault):
        scenario = Jailbreak(objective_scorer=_scorer())
    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(return_value=_resolved_groups({"harmbench": 4}))

    estimate = await scenario.get_default_run_size_estimate_async()
    assert estimate.estimated_attack_count is None
    assert [component.count for component in estimate.components] == [4, 8, 8]
    assert "2 template(s) x 4 selected logical seed group(s) x 1 selected" in estimate.note
    assert "Baseline adds one unit per selected seed group (4 units)" in estimate.note
    assert "num_jailbreaks selects templates" in estimate.components[1].note
    assert "20" in estimate.note
    assert estimate.effective_parameters == {
        "num_jailbreaks": 2,
        "num_jailbreak_attempts": 1,
    }


@pytest.mark.usefixtures("patch_central_database")
async def test_jailbreak_configured_estimate_counts_prompt_sending_without_baseline() -> None:
    """Two templates over four groups produce eight units when baseline is disabled."""
    with patch("pyrit.scenario.scenarios.airt.jailbreak._build_jailbreak_technique", return_value=_JailbreakDefault):
        scenario = Jailbreak(objective_scorer=_scorer())
    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(return_value=_resolved_groups({"harmbench": 4}))
    scenario.set_params_from_args(
        args={
            "scenario_techniques": [_JailbreakDefault.PROMPT_SENDING],
            "include_baseline": False,
            "num_jailbreaks": 2,
            "num_jailbreak_attempts": 1,
        }
    )

    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.estimated_attack_count == 8
    assert [component.count for component in estimate.components] == [8]
    assert "2 template(s) x 4 selected logical seed group(s) x 1 selected" in estimate.note
    assert "Baseline is disabled" in estimate.note


@pytest.mark.usefixtures("patch_central_database")
async def test_jailbreak_configured_estimate_reports_explicit_template_names() -> None:
    """Explicit template names replace the random template-count parameter in the estimate."""
    with patch("pyrit.scenario.scenarios.airt.jailbreak._build_jailbreak_technique", return_value=_JailbreakDefault):
        scenario = Jailbreak(objective_scorer=_scorer())
    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(return_value=_resolved_groups({"harmbench": 2}))
    scenario.set_params_from_args(
        args={
            "scenario_techniques": [_JailbreakDefault.PROMPT_SENDING],
            "include_baseline": False,
            "jailbreak_names": ["aim.yaml", "dan.yaml"],
            "num_jailbreak_attempts": 1,
        }
    )

    estimate = await scenario.get_run_size_estimate_async()

    assert estimate.effective_parameters == {
        "jailbreak_names": ["aim.yaml", "dan.yaml"],
        "num_jailbreak_attempts": 1,
    }


@pytest.mark.usefixtures("patch_central_database")
async def test_jailbreak_configured_estimate_counts_prompt_sending_with_baseline() -> None:
    """Two templates over four groups plus baseline produce twelve planned units."""
    with patch("pyrit.scenario.scenarios.airt.jailbreak._build_jailbreak_technique", return_value=_JailbreakDefault):
        scenario = Jailbreak(objective_scorer=_scorer())
    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(return_value=_resolved_groups({"harmbench": 4}))
    scenario.set_params_from_args(
        args={
            "scenario_techniques": [_JailbreakDefault.PROMPT_SENDING],
            "include_baseline": True,
            "num_jailbreaks": 2,
            "num_jailbreak_attempts": 1,
        }
    )

    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.estimated_attack_count == 12
    assert [component.count for component in estimate.components] == [4, 8]
    assert estimate.components[0].is_baseline is True
    assert "Baseline adds one unit per selected seed group (4 units)" in estimate.note


@pytest.mark.usefixtures("patch_central_database")
async def test_jailbreak_configured_estimate_uses_target_capability() -> None:
    """A capable selected target makes native system-prompt delivery exact."""
    with patch("pyrit.scenario.scenarios.airt.jailbreak._build_jailbreak_technique", return_value=_JailbreakDefault):
        scenario = Jailbreak(objective_scorer=_scorer())
    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(return_value=_resolved_groups({"harmbench": 4}))
    objective_target = MagicMock(spec=PromptTarget)
    objective_target.get_identifier.return_value = ComponentIdentifier(class_name="CapableTarget", class_module="test")
    objective_target.configuration.includes.return_value = True
    scenario.set_params_from_args(
        args={
            "objective_target": objective_target,
            "scenario_techniques": [_JailbreakDefault.SYSTEM_PROMPT],
            "include_baseline": False,
            "num_jailbreaks": 2,
            "num_jailbreak_attempts": 1,
        }
    )

    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.estimated_attack_count == 8
    assert [component.count for component in estimate.components] == [0, 8]
    objective_target.send_prompt_async.assert_not_called()


@pytest.mark.usefixtures("patch_central_database")
async def test_jailbreak_configured_estimate_rejects_incapable_system_delivery() -> None:
    """System-only delivery is invalid when the selected target lacks native capabilities."""
    with patch("pyrit.scenario.scenarios.airt.jailbreak._build_jailbreak_technique", return_value=_JailbreakDefault):
        scenario = Jailbreak(objective_scorer=_scorer())
    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(return_value=_resolved_groups({"harmbench": 4}))
    objective_target = MagicMock(spec=PromptTarget)
    objective_target.get_identifier.return_value = ComponentIdentifier(
        class_name="IncapableTarget", class_module="test"
    )
    objective_target.configuration.includes.return_value = False
    scenario.set_params_from_args(
        args={
            "objective_target": objective_target,
            "scenario_techniques": [_JailbreakDefault.SYSTEM_PROMPT],
            "include_baseline": False,
            "num_jailbreaks": 2,
            "num_jailbreak_attempts": 1,
        }
    )

    with pytest.raises(ValueError, match="requires an objective target with editable history"):
        await scenario.get_run_size_estimate_async()

    objective_target.send_prompt_async.assert_not_called()


@pytest.mark.usefixtures("patch_central_database")
async def test_encoding_estimate_counts_concrete_converter_and_decode_variants() -> None:
    """Encoding expands thirteen catalog techniques into fifteen concrete converter variants."""
    scenario = Encoding(objective_scorer=_scorer())
    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(return_value=_resolved_groups({"encoding": 2}))

    estimate = await scenario.get_default_run_size_estimate_async()
    assert estimate.estimated_attack_count == 152
    assert "2 selected seed groups x 15 concrete converter variants x 5 prompt configurations" in (
        estimate.components[0].note or ""
    )


@pytest.mark.usefixtures("patch_central_database")
async def test_web_injection_estimate_uses_synthesized_technique_populations() -> None:
    """Web injection reports raw sources and capped synthesized populations separately."""
    scenario = WebInjection()
    dataset_values = {
        scenario.DATASET_EXAMPLE_DOMAINS: ["example.com", "contoso.com"],
        scenario.DATASET_MARKDOWN_JS: ["javascript:alert(1)"],
        scenario.DATASET_WEB_HTML_JS: ["<script>alert(1)</script>"],
        scenario.DATASET_NORMAL_INSTRUCTIONS: ["Write a poem.", "Explain gravity."],
    }
    with patch.object(scenario, "_load_dataset_values", return_value=dataset_values):
        estimate = await scenario.get_default_run_size_estimate_async()

    synthesized = [dataset for dataset in estimate.datasets if dataset.kind == "synthesized"]
    synthesized_count = sum(dataset.selected_seed_group_count for dataset in synthesized)
    assert len(synthesized) == len(scenario._scenario_techniques)
    assert estimate.estimated_attack_count == synthesized_count * 2
    assert estimate.components[-1].label == "Baseline"


@pytest.mark.usefixtures("patch_central_database")
async def test_psychosocial_estimate_keeps_sub_harm_baselines_separate() -> None:
    """Psychosocial plans each sub-harm's technique cells and baseline independently."""
    scenario = Psychosocial(
        imminent_crisis_scorer=_scorer(),
        licensed_therapist_scorer=_scorer(),
    )
    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(
        return_value=_resolved_groups({"airt_imminent_crisis": 2, "airt_licensed_therapist": 1})
    )

    estimate = await scenario.get_default_run_size_estimate_async()
    assert estimate.estimated_attack_count == 12
    assert [component.count for component in estimate.components] == [6, 2, 3, 1]


@pytest.mark.usefixtures("patch_central_database")
async def test_adversarial_benchmark_estimate_exposes_per_required_target_formula() -> None:
    """Adversarial benchmark reports a one-target floor before target count is known."""
    with patch(
        "pyrit.scenario.scenarios.benchmark.adversarial._build_benchmark_technique",
        return_value=_TwoTechniqueDefault,
    ):
        scenario = AdversarialBenchmark(objective_scorer=_scorer())
    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(return_value=_resolved_groups({"harmbench": 3}))
    factory = MagicMock()
    factory.seed_technique = None

    with patch(
        "pyrit.scenario.scenarios.benchmark.adversarial.resolve_technique_factories_for_techniques",
        return_value={"one": factory, "two": factory},
    ):
        estimate = await scenario.get_default_run_size_estimate_async()

    assert estimate.estimated_attack_count is None
    assert estimate.minimum_attack_count == 6
    assert estimate.maximum_attack_count is None
    assert [component.count for component in estimate.components] == [3, 3]
    assert "adversarial_targets" in estimate.note


@pytest.mark.parametrize(
    ("use_cached", "expected_total"),
    [
        (False, 6),
        (True, None),
    ],
)
@pytest.mark.usefixtures("patch_central_database")
async def test_adversarial_benchmark_resolves_targets_and_filters_each_technique(
    *,
    use_cached: bool,
    expected_total: int | None,
) -> None:
    """Benchmark sizing resolves target names and reports uncached compatible candidates."""
    with patch(
        "pyrit.scenario.scenarios.benchmark.adversarial._build_benchmark_technique",
        return_value=_TwoTechniqueDefault,
    ):
        scenario = AdversarialBenchmark(objective_scorer=_scorer(), use_cached=use_cached)
    scenario.set_params_from_args(args={"adversarial_targets": ["target-a", "target-b"]})
    compatible = _seed_group("compatible")
    incompatible = AttackSeedGroup(
        seeds=[
            SeedObjective(value="incompatible"),
            SeedPrompt(value="user", data_type="text", role="user", sequence=0),
            SeedPrompt(value="assistant", data_type="text", role="assistant", sequence=1),
            SeedPrompt(value="user again", data_type="text", role="user", sequence=2),
        ]
    )
    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(
        return_value=(
            {"harmbench": [compatible, incompatible]},
            [
                ScenarioDatasetSummary(
                    name="harmbench",
                    logical_seed_group_count=2,
                    selected_seed_group_count=2,
                )
            ],
        )
    )
    resolve_targets = MagicMock(return_value=[MagicMock(spec=PromptTarget), MagicMock(spec=PromptTarget)])
    scenario._resolve_adversarial_targets = resolve_targets
    plain_factory = MagicMock()
    plain_factory.seed_technique = None
    conversation_factory = MagicMock()
    conversation_factory.seed_technique = AttackTechniqueSeedGroup(
        seeds=[
            SeedSimulatedConversation(
                adversarial_chat_system_prompt_path="fake.yaml",
                num_turns=3,
            )
        ]
    )

    with patch(
        "pyrit.scenario.scenarios.benchmark.adversarial.resolve_technique_factories_for_techniques",
        return_value={"one": plain_factory, "two": conversation_factory},
    ):
        estimate = await scenario.get_run_size_estimate_async()

    resolve_targets.assert_called_once_with(target_names=["target-a", "target-b"])
    assert estimate.estimated_attack_count == expected_total
    assert [(component.label, component.count) for component in estimate.components] == [("one", 4), ("two", 2)]


@pytest.mark.parametrize(
    ("compatibility_bounds", "expected_minimum", "expected_maximum"),
    [
        (None, None, None),
        ({"one": (0, 1), "two": (1, 1)}, 1, 2),
    ],
)
@pytest.mark.usefixtures("patch_central_database")
async def test_adversarial_benchmark_binding_cap_reports_available_bounds(
    compatibility_bounds: dict[str, tuple[int, int]] | None,
    expected_minimum: int | None,
    expected_maximum: int | None,
) -> None:
    """A binding cap reports a range only when independent sampling makes one available."""
    with patch(
        "pyrit.scenario.scenarios.benchmark.adversarial._build_benchmark_technique",
        return_value=_TwoTechniqueDefault,
    ):
        scenario = AdversarialBenchmark(objective_scorer=_scorer())
    scenario.set_params_from_args(args={"adversarial_targets": ["target-a"]})
    selected_group = _seed_group("selected")

    async def resolve_groups() -> tuple[dict[str, list[AttackSeedGroup]], list[ScenarioDatasetSummary]]:
        scenario._estimate_has_binding_size_cap = True
        return (
            {"harmbench": [selected_group]},
            [
                ScenarioDatasetSummary(
                    name="harmbench",
                    logical_seed_group_count=2,
                    selected_seed_group_count=1,
                )
            ],
        )

    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(side_effect=resolve_groups)
    scenario._resolve_adversarial_targets = MagicMock(return_value=[MagicMock(spec=PromptTarget)])
    scenario._get_technique_compatibility_bounds = MagicMock(return_value=compatibility_bounds)
    factory = MagicMock()
    factory.seed_technique = None

    with patch(
        "pyrit.scenario.scenarios.benchmark.adversarial.resolve_technique_factories_for_techniques",
        return_value={"one": factory, "two": factory},
    ):
        estimate = await scenario.get_run_size_estimate_async()

    assert estimate.estimated_attack_count is None
    assert estimate.minimum_attack_count == expected_minimum
    assert estimate.maximum_attack_count == expected_maximum


@pytest.mark.usefixtures("patch_central_database")
async def test_foundry_estimate_counts_composites_instead_of_flattened_techniques() -> None:
    """Each Foundry composite contributes one selected seed population."""
    scenario = RedTeamAgent(
        adversarial_chat=MagicMock(spec=PromptTarget),
        attack_scoring_config=AttackScoringConfig(objective_scorer=_scorer()),
    )
    scenario.set_params_from_args(
        args={
            "scenario_techniques": [
                FoundryComposite(
                    attack=FoundryTechnique.Crescendo,
                    converters=[FoundryTechnique.Base64, FoundryTechnique.ROT13],
                ),
                FoundryComposite(attack=None, converters=[FoundryTechnique.Tense]),
            ],
            "include_baseline": False,
        }
    )
    scenario._resolve_dataset_groups_for_estimate_async = AsyncMock(return_value=_resolved_groups({"harmbench": 3}))

    estimate = await scenario.get_run_size_estimate_async()

    assert estimate.estimated_attack_count == 6
    assert len(estimate.components) == 2
    assert [component.count for component in estimate.components] == [3, 3]
