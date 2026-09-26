# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ScenarioRegistry._build_metadata and create_and_initialize_async."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.models import (
    ScenarioDatasetSelection,
    ScenarioDatasetSelectionOverrideScope,
    ScenarioDatasetSizeLimitDefaultScope,
    ScenarioDatasetSizeLimitOverrideScope,
)
from pyrit.registry.components.scenario_registry import ScenarioRegistry
from pyrit.scenario.core import (
    BaselineAttackPolicy,
    CompoundDatasetAttackConfiguration,
    DatasetAttackConfiguration,
    DatasetConfiguration,
    DatasetConstraintError,
    Scenario,
    ScenarioTechnique,
)
from pyrit.scenario.scenarios.airt.psychosocial import Psychosocial
from pyrit.scenario.scenarios.garak.api_key import ApiKey
from pyrit.scenario.scenarios.garak.encoding import Encoding
from pyrit.scenario.scenarios.garak.figstep import FigStep
from pyrit.scenario.scenarios.garak.package_hallucination import PackageHallucination
from pyrit.scenario.scenarios.garak.prompt_inject import PromptInject
from pyrit.scenario.scenarios.garak.system_prompt_extraction import SystemPromptExtraction
from pyrit.scenario.scenarios.garak.web_injection import WebInjection


class _NotNoArgScenario:
    """A scenario-like stub whose constructor requires arguments."""

    @classmethod
    def supported_parameters(cls):
        return []

    def __init__(self, *, required_arg) -> None:
        self.required_arg = required_arg


class _MetadataTechnique(ScenarioTechnique):
    """Technique catalog for metadata expansion."""

    ALL = ("all", {"all"})
    DEFAULT = ("default", {"default"})
    ONE = ("one", {"default"}, "Runs the first attack.")
    TWO = ("two", {"default"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        """Return aggregate tags."""
        return {"all", "default"}

    @classmethod
    def default(cls) -> "_MetadataTechnique":
        """Return the default aggregate."""
        return cls.DEFAULT


class _MetadataScenario:
    """Minimal scenario-shaped metadata source."""

    BASELINE_ATTACK_POLICY = BaselineAttackPolicy.Enabled
    uses_default_adversarial_target = False

    @classmethod
    def supported_parameters(cls):
        """Return no custom parameters."""
        return []

    def __init__(self) -> None:
        self._version = 1
        self._technique_class = _MetadataTechnique
        self._default_technique = _MetadataTechnique.DEFAULT
        self._default_dataset_config = DatasetAttackConfiguration(dataset_names=["sample"])

    def _resolve_scenario_techniques(self, *, scenario_techniques):
        """Resolve the concrete defaults."""
        return _MetadataTechnique.resolve(scenario_techniques, default=self._default_technique)

    def get_dataset_size_limit_override_scope(self) -> ScenarioDatasetSizeLimitOverrideScope:
        """Return the conventional single-dataset override scope."""
        return ScenarioDatasetSizeLimitOverrideScope.PerDataset

    def get_dataset_selection(self) -> ScenarioDatasetSelection:
        """Return unrestricted dataset-name selection."""
        return ScenarioDatasetSelection()


class _MarkdownMetadataScenario(_MetadataScenario):
    """
    First paragraph with ``literal`` text.

    - Item one
    - [Split link](
      https://example.com)

    <script>alert("untrusted")</script>
    """


def test_build_metadata_raises_when_scenario_requires_constructor_args() -> None:
    """Scenarios that cannot be instantiated with no args must surface a clear error."""
    registry = ScenarioRegistry()

    with pytest.raises(TypeError, match="must be instantiable with no arguments"):
        registry._build_metadata("not_no_arg", _NotNoArgScenario)


def test_build_metadata_expands_ordered_default_techniques() -> None:
    """Catalog metadata exposes concrete defaults rather than only the aggregate name."""
    metadata = ScenarioRegistry()._build_metadata("sample", _MetadataScenario)

    assert metadata.default_technique == "default"
    assert metadata.uses_default_adversarial_target is False
    assert metadata.default_techniques == ("one", "two")
    assert metadata.technique_summaries[0].model_dump() == {
        "name": "one",
        "description": "Runs the first attack.",
        "tags": ["default"],
    }
    assert metadata.technique_summaries[1].model_dump() == {
        "name": "two",
        "description": None,
        "tags": ["default"],
    }
    assert dict(metadata.aggregate_technique_expansions) == {
        "all": ("one", "two"),
        "default": ("one", "two"),
    }
    assert metadata.default_datasets == ("sample",)
    assert metadata.dataset_selection.override_scope is ScenarioDatasetSelectionOverrideScope.Any
    assert metadata.dataset_selection.allowed_names is None
    assert metadata.dataset_size_limit.default_scope is ScenarioDatasetSizeLimitDefaultScope.None_
    assert metadata.dataset_size_limit.override_scope is ScenarioDatasetSizeLimitOverrideScope.PerDataset


@pytest.mark.parametrize(
    ("configuration", "override_scope", "default_scope", "default_count"),
    [
        (
            DatasetAttackConfiguration(dataset_names=["sample"]),
            ScenarioDatasetSizeLimitOverrideScope.PerDataset,
            ScenarioDatasetSizeLimitDefaultScope.None_,
            None,
        ),
        (
            DatasetAttackConfiguration(dataset_names=["sample"]),
            ScenarioDatasetSizeLimitOverrideScope.Unsupported,
            ScenarioDatasetSizeLimitDefaultScope.None_,
            None,
        ),
        (
            DatasetAttackConfiguration(dataset_names=["one", "two"], max_dataset_size=6),
            ScenarioDatasetSizeLimitOverrideScope.Combined,
            ScenarioDatasetSizeLimitDefaultScope.Combined,
            6,
        ),
        (
            CompoundDatasetAttackConfiguration.per_dataset(
                dataset_names=["one", "two"],
                max_dataset_size=4,
            ),
            ScenarioDatasetSizeLimitOverrideScope.PerDataset,
            ScenarioDatasetSizeLimitDefaultScope.PerDataset,
            4,
        ),
        (
            CompoundDatasetAttackConfiguration(
                configurations=[
                    DatasetAttackConfiguration(dataset_names=["one"], max_dataset_size=3),
                    DatasetAttackConfiguration(dataset_names=["two"], max_dataset_size=4),
                ]
            ),
            ScenarioDatasetSizeLimitOverrideScope.PerDataset,
            ScenarioDatasetSizeLimitDefaultScope.Heterogeneous,
            None,
        ),
    ],
)
def test_build_dataset_size_limit_normalizes_configuration_semantics(
    configuration: DatasetConfiguration,
    override_scope: ScenarioDatasetSizeLimitOverrideScope,
    default_scope: ScenarioDatasetSizeLimitDefaultScope,
    default_count: int | None,
) -> None:
    """Catalog metadata preserves uncapped, combined, per-dataset, and heterogeneous defaults."""
    limit = ScenarioRegistry._build_dataset_size_limit(
        default_dataset_config=configuration,
        override_scope=override_scope,
    )

    assert limit.default_scope is default_scope
    assert limit.default_count == default_count
    assert limit.override_scope is override_scope


def test_specialized_scenarios_declare_nonstandard_dataset_override_semantics() -> None:
    """Specialized dataset shaping remains explicit in catalog metadata."""
    assert Psychosocial.DATASET_SIZE_LIMIT_OVERRIDE_SCOPE is ScenarioDatasetSizeLimitOverrideScope.PerDataset
    assert WebInjection.DATASET_SIZE_LIMIT_OVERRIDE_SCOPE is ScenarioDatasetSizeLimitOverrideScope.Unsupported
    assert PackageHallucination.DATASET_SIZE_LIMIT_OVERRIDE_SCOPE is ScenarioDatasetSizeLimitOverrideScope.Unsupported
    assert SystemPromptExtraction.DATASET_SIZE_LIMIT_OVERRIDE_SCOPE is ScenarioDatasetSizeLimitOverrideScope.Unsupported


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("name", "scenario_class"),
    [
        ("garak.package_hallucination", PackageHallucination),
        ("garak.system_prompt_extraction", SystemPromptExtraction),
    ],
)
def test_synthesized_scenario_catalog_disables_ignored_dataset_cap(name: str, scenario_class: type[Scenario]) -> None:
    metadata = ScenarioRegistry()._build_metadata(name, scenario_class)
    assert metadata.dataset_size_limit.override_scope is ScenarioDatasetSizeLimitOverrideScope.Unsupported


@pytest.mark.parametrize(
    ("scenario_class", "scope"),
    [
        (Psychosocial, ScenarioDatasetSelectionOverrideScope.Unsupported),
        (Encoding, ScenarioDatasetSelectionOverrideScope.Fixed),
        (FigStep, ScenarioDatasetSelectionOverrideScope.OneOf),
        (ApiKey, ScenarioDatasetSelectionOverrideScope.FixedSet),
        (PromptInject, ScenarioDatasetSelectionOverrideScope.FixedSet),
        (WebInjection, ScenarioDatasetSelectionOverrideScope.FixedSet),
        (SystemPromptExtraction, ScenarioDatasetSelectionOverrideScope.FixedSet),
        (PackageHallucination, ScenarioDatasetSelectionOverrideScope.Unsupported),
    ],
)
def test_specialized_dataset_selection_is_declared(
    scenario_class: type[Scenario], scope: ScenarioDatasetSelectionOverrideScope
) -> None:
    assert scenario_class.DATASET_SELECTION_OVERRIDE_SCOPE is scope


@pytest.mark.usefixtures("patch_central_database")
def test_encoding_catalog_exposes_fixed_default_selection() -> None:
    metadata = ScenarioRegistry()._build_metadata("garak.encoding", Encoding)

    assert metadata.default_datasets == ("garak_slur_terms_en", "garak_web_html_js")
    assert metadata.dataset_selection.model_dump(mode="json") == {
        "override_scope": "fixed",
        "allowed_names": ["garak_slur_terms_en", "garak_web_html_js"],
    }


def test_figstep_catalog_lists_both_single_dataset_options() -> None:
    """One-of metadata exposes valid alternatives rather than only the default."""
    scenario = object.__new__(FigStep)
    scenario._default_dataset_config = DatasetAttackConfiguration(dataset_names=["figstep"])
    selection = scenario.get_dataset_selection()

    assert selection.override_scope is ScenarioDatasetSelectionOverrideScope.OneOf
    assert selection.allowed_names == ["figstep", "figstep_pro"]


def test_build_metadata_reports_scenario_owned_adversarial_usage() -> None:
    """The catalog keeps a scenario's declared use of the shared target."""

    class AdversarialScenario(_MetadataScenario):
        uses_default_adversarial_target = True

    metadata = ScenarioRegistry()._build_metadata("adversarial", AdversarialScenario)
    assert metadata.uses_default_adversarial_target is True


def test_build_metadata_preserves_structured_markdown_separately() -> None:
    """Scenario metadata keeps plain compatibility text and Markdown source."""
    metadata = ScenarioRegistry()._build_metadata("markdown", _MarkdownMetadataScenario)

    assert "\n" not in metadata.class_description
    assert metadata.description_markdown == (
        "First paragraph with ``literal`` text.\n\n"
        "- Item one\n"
        "- [Split link](\n"
        "  https://example.com)\n\n"
        '<script>alert("untrusted")</script>'
    )


async def test_create_and_initialize_async_creates_sets_params_and_initializes() -> None:
    """The registry owns build + set-params + initialize and returns the scenario."""
    registry = ScenarioRegistry()

    scenario = MagicMock()
    scenario.initialize_async = AsyncMock()
    target = MagicMock()

    registry.create_instance = MagicMock(return_value=scenario)  # type: ignore[method-assign]

    result = await registry.create_and_initialize_async(
        "my.scenario",
        scenario_params={"foo": "bar"},
        scenario_result_id="sr-1",
        initial_metadata={"scheduler_managed_by": "test"},
        objective_target=target,
        max_concurrency=2,
    )

    assert result is scenario
    registry.create_instance.assert_called_once_with("my.scenario", scenario_result_id="sr-1")
    scenario.set_scenario_registry_name.assert_called_once_with(scenario_registry_name="my.scenario")
    scenario.set_initial_metadata.assert_called_once_with(metadata={"scheduler_managed_by": "test"})
    scenario.set_params_from_args.assert_called_once_with(
        args={"foo": "bar", "objective_target": target, "max_concurrency": 2}
    )
    scenario.initialize_async.assert_awaited_once_with()


async def test_create_and_estimate_async_configures_without_initializing() -> None:
    """Configured estimation uses the registry parameter lifecycle without creating a run."""
    registry = ScenarioRegistry()
    scenario = MagicMock()
    estimate = MagicMock()
    scenario.get_run_size_estimate_async = AsyncMock(return_value=estimate)
    registry.create_instance = MagicMock(return_value=scenario)  # type: ignore[method-assign]

    result = await registry.create_and_estimate_async(
        name="my.scenario",
        scenario_params={"num_jailbreaks": 2},
        scenario_techniques=["prompt_sending"],
        include_baseline=False,
    )

    assert result is estimate
    registry.create_instance.assert_called_once_with("my.scenario")
    scenario.set_scenario_registry_name.assert_called_once_with(scenario_registry_name="my.scenario")
    scenario.set_params_from_args.assert_called_once_with(
        args={
            "num_jailbreaks": 2,
            "scenario_techniques": ["prompt_sending"],
            "include_baseline": False,
        }
    )
    scenario.get_run_size_estimate_async.assert_awaited_once_with(target_is_configured=False)
    scenario.initialize_async.assert_not_called()


async def test_create_and_estimate_async_uses_read_only_dataset_resolution() -> None:
    """The registry estimate lifecycle cannot fetch or persist a missing dataset."""
    registry = ScenarioRegistry()
    memory = MagicMock()
    memory.get_seeds.return_value = []
    memory.add_seed_datasets_to_memory_async = AsyncMock()
    config = DatasetAttackConfiguration(dataset_names=["missing"])

    async def estimate_async(*, target_is_configured: bool) -> MagicMock:
        assert target_is_configured is False
        await config.get_attack_seed_groups_async()
        return MagicMock()

    scenario = MagicMock()
    scenario.get_run_size_estimate_async = AsyncMock(side_effect=estimate_async)
    registry.create_instance = MagicMock(return_value=scenario)  # type: ignore[method-assign]

    with (
        patch(
            "pyrit.scenario.core.dataset_configuration.CentralMemory.get_memory_instance",
            return_value=memory,
        ),
        patch.object(config, "_fetch_dataset_async", new_callable=AsyncMock) as fetch_dataset,
        pytest.raises(DatasetConstraintError, match="read-only resolution"),
    ):
        await registry.create_and_estimate_async(name="my.scenario")

    fetch_dataset.assert_not_awaited()
    memory.add_seed_datasets_to_memory_async.assert_not_awaited()


@pytest.mark.parametrize("parameter_name", ["include_baseline", "dataset_config"])
async def test_registry_rejects_conflicting_parameter_ownership(parameter_name: str) -> None:
    """Dedicated request fields cannot also be supplied through scenario_params."""
    registry = ScenarioRegistry()
    scenario = MagicMock()
    registry.create_instance = MagicMock(return_value=scenario)  # type: ignore[method-assign]

    with pytest.raises(ValueError, match=parameter_name):
        await registry.create_and_estimate_async(
            name="my.scenario",
            scenario_params={parameter_name: "scenario-owned"},
            **{parameter_name: "request-owned"},
        )


async def test_create_and_initialize_async_omits_result_id_when_none() -> None:
    """When no scenario_result_id is supplied, it is not forwarded to the constructor."""
    registry = ScenarioRegistry()

    scenario = MagicMock()
    scenario.initialize_async = AsyncMock()
    registry.create_instance = MagicMock(return_value=scenario)  # type: ignore[method-assign]

    target = MagicMock()
    await registry.create_and_initialize_async("my.scenario", objective_target=target)

    registry.create_instance.assert_called_once_with("my.scenario")
    scenario.set_scenario_registry_name.assert_called_once_with(scenario_registry_name="my.scenario")
    scenario.set_params_from_args.assert_called_once_with(args={"objective_target": target})
    scenario.initialize_async.assert_awaited_once_with()
