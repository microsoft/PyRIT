# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared scenario launch and estimate configuration resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyrit.registry import ScenarioRegistry, TargetRegistry
from pyrit.scenario.core.scenario_target_defaults import validate_default_adversarial_target
from pyrit.scenario.core.technique_tokens import resolve_technique_tokens

if TYPE_CHECKING:
    from pyrit.converter import Converter
    from pyrit.models.scenario_dataset import ScenarioDatasetRequest
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario import Scenario


class ScenarioConfigurationResolver:
    """Resolve registry-backed scenario inputs for launch and estimation."""

    @classmethod
    def resolve_adversarial_target(cls, *, target_name: str | None) -> PromptTarget | None:
        """
        Resolve and validate an optional request-scoped adversarial default.

        Returns:
            PromptTarget | None: The selected registered instance, or no override.

        Raises:
            ValueError: If the explicit selection is missing or lacks required capabilities.
        """
        if target_name is None:
            return None
        target = cls.resolve_target(target_name=target_name)
        validate_default_adversarial_target(target)
        return target

    @staticmethod
    def resolve_scenario_class(*, scenario_name: str) -> type[Scenario]:
        """
        Resolve a registered scenario class.

        Returns:
            type[Scenario]: The registered scenario class.

        Raises:
            ValueError: If the scenario name is not registered.
        """
        try:
            return ScenarioRegistry.get_registry_singleton().get_class(scenario_name)
        except KeyError as exc:
            raise ValueError(str(exc)) from None

    @staticmethod
    def resolve_target(*, target_name: str) -> PromptTarget:
        """
        Resolve a registered target instance.

        Returns:
            PromptTarget: The registered target.

        Raises:
            ValueError: If the target name is not registered.
        """
        instances = TargetRegistry.get_registry_singleton().instances
        objective_target = instances.get(target_name)
        if objective_target is not None:
            return objective_target

        available_names = instances.get_names()
        if not available_names:
            raise ValueError(
                f"Target '{target_name}' not found. The target registry is empty. "
                "Make sure to include an initializer that registers targets "
                "(e.g., initializers: ['target'])."
            )
        raise ValueError(
            f"Target '{target_name}' not found in registry. Available targets: {', '.join(available_names)}"
        )

    @classmethod
    def resolve_configuration(
        cls,
        *,
        scenario_name: str,
        scenario_class: type[Scenario],
        objective_target: Any | None = None,
        techniques: list[str] | None = None,
        datasets: ScenarioDatasetRequest | None = None,
        include_baseline: bool | None = None,
        max_concurrency: int | None = None,
        max_retries: int | None = None,
        memory_labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Resolve shared launch and estimate fields into scenario parameters.

        Dataset edits pass through unchanged; the scenario applies and validates them.

        Returns:
            dict[str, Any]: Values accepted by ``Scenario.set_params_from_args``.

        Raises:
            ValueError: If techniques or dataset overrides are invalid.
        """
        resolved: dict[str, Any] = {}
        if objective_target is not None:
            resolved["objective_target"] = objective_target
        if max_concurrency is not None:
            resolved["max_concurrency"] = max_concurrency
        if max_retries is not None:
            resolved["max_retries"] = max_retries
        if include_baseline is not None:
            resolved["include_baseline"] = include_baseline
        if memory_labels:
            resolved["memory_labels"] = memory_labels

        if datasets is not None:
            resolved["dataset_overrides"] = datasets
        if not techniques:
            return resolved

        try:
            introspection_instance = scenario_class()  # type: ignore[ty:missing-argument]
        except Exception as exc:
            raise ValueError(
                f"Cannot resolve runtime configuration for scenario '{scenario_name}': "
                f"scenario class is not instantiable without arguments ({exc})."
            ) from exc

        if techniques:
            technique_enums, technique_converters = cls.resolve_techniques_and_converters(
                tokens=techniques,
                technique_class=introspection_instance._technique_class,
                scenario_name=scenario_name,
            )
            resolved["scenario_techniques"] = technique_enums
            if technique_converters:
                resolved["technique_converters"] = technique_converters

        return resolved

    @classmethod
    def resolve_techniques_and_converters(
        cls,
        *,
        tokens: list[str],
        technique_class: type[Any],
        scenario_name: str,
    ) -> tuple[list[Any], dict[str, list[Converter]]]:
        """
        Resolve technique tokens and their additive converter modifiers.

        Resolution itself lives with the technique catalog in ``pyrit.scenario.core``, so
        the launch path and the preset check cannot disagree about what a token means.
        This adapter supplies the launch path's contract: stop at the first bad token
        rather than reporting every problem.

        Returns:
            tuple[list[Any], dict[str, list[Converter]]]: Selected enum members and
                converters keyed by concrete technique name.

        Raises:
            ValueError: If a technique or converter modifier is invalid.
        """
        resolution = resolve_technique_tokens(
            tokens=tokens,
            technique_class=technique_class,
            scenario_name=scenario_name,
        )
        if resolution.issues:
            raise ValueError(resolution.issues[0].message)
        return resolution.techniques, resolution.technique_converters
