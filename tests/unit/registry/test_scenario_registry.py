# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for ScenarioRegistry._build_metadata and create_and_initialize_async."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.registry.components.scenario_registry import ScenarioRegistry
from pyrit.scenario.core import BaselineAttackPolicy, ScenarioTechnique


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
        objective_target=target,
        max_concurrency=2,
    )

    assert result is scenario
    registry.create_instance.assert_called_once_with("my.scenario", scenario_result_id="sr-1")
    scenario.set_scenario_registry_name.assert_called_once_with(scenario_registry_name="my.scenario")
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
