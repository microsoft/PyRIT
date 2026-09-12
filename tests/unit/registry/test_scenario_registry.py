# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ScenarioRegistry._build_metadata and create_and_initialize_async."""

from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.registry import ScenarioRegistry
from pyrit.scenario import (
    BaselineAttackPolicy,
    CompoundDatasetAttackConfiguration,
    DatasetAttackConfiguration,
    ScenarioTechnique,
)
from pyrit.scenario.scenarios.adaptive import TextAdaptive
from pyrit.scenario.scenarios.airt import Psychosocial
from pyrit.scenario.scenarios.garak import WebInjection


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

    @classmethod
    def supported_parameters(cls):
        """Return no custom parameters."""
        return []

    def __init__(self) -> None:
        self._version = 1
        self._technique_class = _MetadataTechnique
        self._default_technique = _MetadataTechnique.DEFAULT
        self._default_dataset_config = MagicMock(dataset_names=["sample"])

    def _resolve_scenario_techniques(self, *, scenario_techniques):
        """Resolve the concrete defaults."""
        return _MetadataTechnique.resolve(scenario_techniques, default=self._default_technique)

    def get_dataset_size_limit_override_scope(self) -> Literal["per_dataset"]:
        """Return the test scenario's conventional single-dataset override scope."""
        return "per_dataset"


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


@pytest.mark.parametrize(
    ("configuration", "declared_override_scope", "default_scope", "default_count", "override_scope"),
    [
        (DatasetAttackConfiguration(dataset_names=["sample"]), "per_dataset", "none", None, "per_dataset"),
        (
            DatasetAttackConfiguration(dataset_names=["one", "two"]),
            "per_dataset",
            "none",
            None,
            "per_dataset",
        ),
        (
            DatasetAttackConfiguration(dataset_names=["one", "two"]),
            "unsupported",
            "none",
            None,
            "unsupported",
        ),
        (
            DatasetAttackConfiguration(dataset_names=["one", "two"], max_dataset_size=6),
            "combined",
            "combined",
            6,
            "combined",
        ),
        (
            CompoundDatasetAttackConfiguration.per_dataset(
                dataset_names=["one", "two"],
                max_dataset_size=4,
            ),
            "per_dataset",
            "per_dataset",
            4,
            "per_dataset",
        ),
        (
            CompoundDatasetAttackConfiguration(
                configurations=[
                    DatasetAttackConfiguration(dataset_names=["one"], max_dataset_size=3),
                    DatasetAttackConfiguration(dataset_names=["two"], max_dataset_size=4),
                ]
            ),
            "per_dataset",
            "heterogeneous",
            None,
            "per_dataset",
        ),
    ],
)
def test_build_dataset_size_limit_normalizes_configuration_semantics(
    configuration: DatasetAttackConfiguration,
    declared_override_scope: Literal["per_dataset", "combined", "unsupported"],
    default_scope: str,
    default_count: int | None,
    override_scope: str,
) -> None:
    """Catalog limit metadata preserves no-cap, combined, per-dataset, and heterogeneous defaults."""
    limit = ScenarioRegistry._build_dataset_size_limit(
        default_dataset_config=configuration,
        override_scope=declared_override_scope,
    )

    assert limit.default_scope == default_scope
    assert limit.default_count == default_count
    assert limit.override_scope == override_scope


def test_specialized_scenarios_declare_nonstandard_dataset_override_semantics() -> None:
    """Catalog metadata can remain truthful when a scenario reshapes or ignores generic dataset caps."""
    assert Psychosocial.DATASET_SIZE_LIMIT_OVERRIDE_SCOPE == "per_dataset"
    assert WebInjection.DATASET_SIZE_LIMIT_OVERRIDE_SCOPE == "unsupported"


def test_text_adaptive_metadata_exposes_per_dataset_default_limit() -> None:
    """TextAdaptive publishes its canonical four-objective child cap without scenario-name special cases."""
    with (
        patch.object(TextAdaptive, "_get_default_objective_scorer", return_value=MagicMock()),
        patch("pyrit.scenario.core.scenario.CentralMemory.get_memory_instance", return_value=MagicMock()),
    ):
        metadata = ScenarioRegistry()._build_metadata("adaptive.text_adaptive", TextAdaptive)

    assert metadata.dataset_size_limit.default_scope == "per_dataset"
    assert metadata.dataset_size_limit.default_count == 4
    assert metadata.dataset_size_limit.override_scope == "per_dataset"


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
