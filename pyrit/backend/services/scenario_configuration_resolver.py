# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared scenario launch and estimate configuration resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyrit.models import (
    ScenarioDatasetSelection,
    ScenarioDatasetSelectionOverrideScope,
    ScenarioDatasetSizeLimitOverrideScope,
)
from pyrit.registry import ConverterRegistry, ScenarioRegistry, TargetRegistry
from pyrit.scenario.core.dataset_configuration import (
    CompoundDatasetAttackConfiguration,
    DatasetAttackConfiguration,
    DatasetConfiguration,
)
from pyrit.scenario.core.scenario_target_defaults import validate_default_adversarial_target

if TYPE_CHECKING:
    from pyrit.converter import Converter
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario import Scenario

_CONVERTER_MODIFIER_PREFIX = "converter."


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
        dataset_names: list[str] | None = None,
        max_dataset_size: int | None = None,
        dataset_filters: dict[str, list[str]] | None = None,
        include_baseline: bool | None = None,
        max_concurrency: int | None = None,
        max_retries: int | None = None,
        memory_labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Resolve shared launch and estimate fields into scenario parameters.

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

        filters = dataset_filters or {}
        needs_introspection = (
            bool(techniques) or dataset_names is not None or max_dataset_size is not None or bool(filters)
        )
        if not needs_introspection:
            return resolved

        try:
            introspection_instance = scenario_class()  # type: ignore[ty:missing-argument]
        except Exception as exc:
            raise ValueError(
                f"Cannot resolve runtime configuration for scenario '{scenario_name}': "
                f"scenario class is not instantiable without arguments ({exc})."
            ) from exc

        if dataset_names is not None:
            cls._validate_dataset_selection(
                scenario_name=scenario_name,
                selection=introspection_instance.get_dataset_selection(),
                dataset_names=dataset_names,
            )

        if techniques:
            technique_enums, technique_converters = cls.resolve_techniques_and_converters(
                tokens=techniques,
                technique_class=introspection_instance._technique_class,
                scenario_name=scenario_name,
            )
            resolved["scenario_techniques"] = technique_enums
            if technique_converters:
                resolved["technique_converters"] = technique_converters

        if dataset_names is not None or max_dataset_size is not None or filters:
            default_config = introspection_instance._default_dataset_config
            override_scope = introspection_instance.get_dataset_size_limit_override_scope()
            resolved["dataset_config"] = cls._resolve_dataset_configuration(
                scenario_name=scenario_name,
                default_config=default_config,
                dataset_names=dataset_names,
                max_dataset_size=max_dataset_size,
                filters=filters,
                override_scope=override_scope,
            )

        return resolved

    @staticmethod
    def _validate_dataset_selection(
        *,
        scenario_name: str,
        selection: ScenarioDatasetSelection,
        dataset_names: list[str],
    ) -> None:
        """Reject explicit dataset-name shapes the scenario cannot interpret."""
        scope = selection.override_scope
        if scope is ScenarioDatasetSelectionOverrideScope.Unsupported:
            raise ValueError(f"Scenario '{scenario_name}' does not support dataset_names overrides.")
        if scope is ScenarioDatasetSelectionOverrideScope.Any:
            return

        allowed = selection.allowed_names
        if allowed is None:
            raise ValueError(f"Scenario '{scenario_name}' has no allowed dataset names configured.")
        if scope is ScenarioDatasetSelectionOverrideScope.Fixed and dataset_names != allowed:
            raise ValueError(f"Scenario '{scenario_name}' requires dataset_names={allowed} in that order.")
        if scope is ScenarioDatasetSelectionOverrideScope.FixedSet and set(dataset_names) != set(allowed):
            raise ValueError(f"Scenario '{scenario_name}' requires exactly these datasets: {allowed}.")
        if scope is ScenarioDatasetSelectionOverrideScope.OneOf and (
            len(dataset_names) != 1 or dataset_names[0] not in allowed
        ):
            raise ValueError(f"Scenario '{scenario_name}' requires exactly one dataset from {allowed}.")

    @classmethod
    def _resolve_dataset_configuration(
        cls,
        *,
        scenario_name: str,
        default_config: DatasetConfiguration,
        dataset_names: list[str] | None,
        max_dataset_size: int | None,
        filters: dict[str, list[str]],
        override_scope: ScenarioDatasetSizeLimitOverrideScope,
    ) -> DatasetConfiguration:
        """
        Apply dataset names, filters, and cap scope without changing their meaning.

        Returns:
            DatasetConfiguration: The effective configuration shared by preview and launch.

        Raises:
            ValueError: If an override is empty, duplicated, unsupported, or cannot preserve configuration shape.
        """
        if dataset_names is not None:
            if not dataset_names:
                raise ValueError("dataset-name overrides require at least one dataset")
            if len(set(dataset_names)) != len(dataset_names):
                raise ValueError("dataset-name overrides cannot contain duplicates")
        if max_dataset_size is not None and override_scope is ScenarioDatasetSizeLimitOverrideScope.Unsupported:
            raise ValueError(f"Scenario '{scenario_name}' does not support max_dataset_size overrides.")

        if isinstance(default_config, CompoundDatasetAttackConfiguration):
            return cls._resolve_compound_dataset_configuration(
                scenario_name=scenario_name,
                default_config=default_config,
                dataset_names=dataset_names,
                max_dataset_size=max_dataset_size,
                filters=filters,
                override_scope=override_scope,
            )

        config = cls._rebuild_dataset_configuration(
            scenario_name=scenario_name,
            default_config=default_config,
            dataset_names=dataset_names,
            filters=filters,
        )
        if max_dataset_size is not None:
            config.max_dataset_size = max_dataset_size

        effective_cap = config.max_dataset_size
        if (
            override_scope is ScenarioDatasetSizeLimitOverrideScope.PerDataset
            and effective_cap is not None
            and len(config.dataset_names) > 1
        ):
            if not isinstance(default_config, DatasetAttackConfiguration):
                raise ValueError(
                    f"Scenario '{scenario_name}' cannot preserve per-dataset cap semantics across multiple datasets "
                    f"with {type(default_config).__name__}."
                )
            children = [
                default_config.with_dataset_names(
                    dataset_names=[name],
                    max_dataset_size=effective_cap,
                    filters=filters or None,
                )
                for name in config.dataset_names
            ]
            compound = CompoundDatasetAttackConfiguration(configurations=children)
            if config.filters:
                compound.update_filters(filters=config.filters)
            return compound
        return config

    @staticmethod
    def _resolve_compound_dataset_configuration(
        *,
        scenario_name: str,
        default_config: CompoundDatasetAttackConfiguration,
        dataset_names: list[str] | None,
        max_dataset_size: int | None,
        filters: dict[str, list[str]],
        override_scope: ScenarioDatasetSizeLimitOverrideScope,
    ) -> CompoundDatasetAttackConfiguration:
        """
        Preserve a compound's child shape while applying the declared cap scope.

        Returns:
            CompoundDatasetAttackConfiguration: The effective compound configuration.

        Raises:
            ValueError: If selected dataset names cannot preserve the compound shape.
        """
        config = default_config
        if dataset_names is not None and dataset_names != default_config.dataset_names:
            try:
                config = default_config.with_dataset_names(
                    dataset_names=dataset_names,
                    filters=filters or None,
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Scenario '{scenario_name}' does not support overriding datasets through "
                    f"its {type(default_config).__name__} configuration: {exc}"
                ) from exc
        elif filters:
            config.update_filters(filters=filters)

        if max_dataset_size is None:
            return config
        if override_scope is ScenarioDatasetSizeLimitOverrideScope.PerDataset:
            config.update_child_max_dataset_size(max_dataset_size=max_dataset_size)
        elif override_scope is ScenarioDatasetSizeLimitOverrideScope.Combined:
            config.max_dataset_size = max_dataset_size
        return config

    @staticmethod
    def _rebuild_dataset_configuration(
        *,
        scenario_name: str,
        default_config: DatasetConfiguration,
        dataset_names: list[str] | None,
        filters: dict[str, list[str]],
    ) -> DatasetConfiguration:
        """
        Rebuild a non-compound configuration only when dataset names change.

        Returns:
            DatasetConfiguration: The existing or safely reconstructed configuration.

        Raises:
            ValueError: If the concrete configuration cannot be reconstructed.
        """
        if dataset_names is None or dataset_names == default_config.dataset_names:
            if filters:
                default_config.update_filters(filters=filters)
            return default_config

        try:
            if isinstance(default_config, DatasetAttackConfiguration):
                return default_config.with_dataset_names(
                    dataset_names=dataset_names,
                    filters=filters or None,
                )
            inherited_filters = default_config.filters
            inherited_filters.update(filters)
            return type(default_config)(
                dataset_names=dataset_names,
                max_dataset_size=default_config.max_dataset_size,
                filters=inherited_filters or None,
            )
        except TypeError as exc:
            raise ValueError(
                f"Scenario '{scenario_name}' does not support overriding dataset names through "
                f"its {type(default_config).__name__} configuration: {exc}"
            ) from exc

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

        Returns:
            tuple[list[Any], dict[str, list[Converter]]]: Selected enum members and
                converters keyed by concrete technique name.

        Raises:
            ValueError: If a technique or converter modifier is invalid.
        """
        technique_enums: list[Any] = []
        technique_converters: dict[str, list[Converter]] = {}
        for token in tokens:
            base_name, _, remainder = token.partition(":")
            modifiers = [modifier for modifier in remainder.split(":") if modifier] if remainder else []
            try:
                technique_enum = technique_class(base_name)
            except ValueError:
                available_techniques = [technique.value for technique in technique_class]
                raise ValueError(
                    f"Technique '{base_name}' not found for scenario '{scenario_name}'. "
                    f"Available: {', '.join(available_techniques)}"
                ) from None
            technique_enums.append(technique_enum)

            converters = cls._resolve_converter_modifiers(modifiers=modifiers, token=token)
            for concrete in technique_class.expand({technique_enum}) if converters else ():
                technique_converters.setdefault(concrete.value, []).extend(converters)

        return technique_enums, technique_converters

    @staticmethod
    def _resolve_converter_modifiers(*, modifiers: list[str], token: str) -> list[Converter]:
        """
        Resolve converter modifiers from one technique token.

        Returns:
            list[Converter]: Registered converter instances in token order.

        Raises:
            ValueError: If a modifier is malformed or references an unknown converter.
        """
        if not modifiers:
            return []

        instances = ConverterRegistry.get_registry_singleton().instances
        converters: list[Converter] = []
        for modifier in modifiers:
            if not modifier.startswith(_CONVERTER_MODIFIER_PREFIX):
                raise ValueError(
                    f"Unknown technique modifier '{modifier}' in '{token}'. "
                    f"Supported modifiers must use the '{_CONVERTER_MODIFIER_PREFIX}' prefix "
                    f"(e.g. '{_CONVERTER_MODIFIER_PREFIX}translation_spanish')."
                )
            converter_name = modifier[len(_CONVERTER_MODIFIER_PREFIX) :]
            converter = instances.get(converter_name)
            if converter is None:
                available = instances.get_names()
                available_text = ", ".join(available) if available else "(none registered)"
                raise ValueError(
                    f"Converter '{converter_name}' in '{token}' is not a registered converter "
                    f"instance. Available converters: {available_text}"
                )
            converters.append(converter)
        return converters
