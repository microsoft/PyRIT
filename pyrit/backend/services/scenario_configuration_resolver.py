# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared scenario launch and estimate configuration resolution."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from pyrit.backend.services.dataset_example_selection import (
    exact_example_dataset_configuration,
    resolve_dataset_example_selection_async,
    scenario_accepts_dataset_config,
    selection_conflicts_with_datasets,
)
from pyrit.models import DatasetExampleSelectionError
from pyrit.models.scenario_readiness import ScenarioConfigurationIssue, ScenarioReadiness
from pyrit.registry import ScenarioRegistry, ScorerRegistry, TargetRegistry
from pyrit.scenario import ExactExampleDatasetConfiguration
from pyrit.scenario.core import override_default_adversarial_target, resolve_technique_tokens
from pyrit.scenario.core.scenario_target_defaults import validate_default_adversarial_target
from pyrit.score import TrueFalseScorer

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pyrit.converter import Converter
    from pyrit.memory import MemoryInterface
    from pyrit.models import DatasetExampleSelection, ScenarioDatasetRequest, ScenarioRunSizeEstimateRequest
    from pyrit.models.catalog import RunScenarioRequest
    from pyrit.models.component_spec import RegisteredComponentReference
    from pyrit.models.technique_edit import TechniqueOverride
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario import DatasetAttackConfiguration, Scenario


class ScenarioConfigurationResolver:
    """Resolve registry-backed scenario inputs for launch and estimation."""

    @classmethod
    def create_configured_scenario(
        cls, *, scenario_name: str, request: RunScenarioRequest | ScenarioRunSizeEstimateRequest
    ) -> Scenario:
        """
        Build configuration only, sharing the launch parameter and reference rules.

        Returns:
            Scenario: Configured, uninitialized scenario.
        """
        scenario_class = cls.resolve_scenario_class(scenario_name=scenario_name)
        params = cls.resolve_configuration(
            scenario_name=scenario_name,
            scenario_class=scenario_class,
            objective_target=cls.resolve_target(target_name=request.target_name) if request.target_name else None,
            scoring_target_name=request.scoring_target_name,
            techniques=request.techniques,
            technique_override=request.technique_override,
            datasets=request.dataset_request,
            include_baseline=request.include_baseline,
        )
        return ScenarioRegistry.get_registry_singleton()._create_and_configure(
            scenario_name,
            params={**(request.scenario_params or {}), **params},
            constructor_kwargs=cls.resolve_constructor_kwargs(
                scenario_class=scenario_class,
                objective_scorer=request.objective_scorer,
            ),
        )

    @classmethod
    def resolve_readiness(
        cls, *, scenario_name: str, request: RunScenarioRequest | ScenarioRunSizeEstimateRequest
    ) -> ScenarioReadiness:
        """
        Preview exactly the roles a configured run requires, without executing work.

        Returns:
            ScenarioReadiness: Effective bindings and actionable issues.
        """
        issues, updates, target = cls.resolve_named_roles(scenario_name=scenario_name, request=request)
        with override_default_adversarial_target(target):
            try:
                scenario = cls.create_configured_scenario(
                    scenario_name=scenario_name, request=request.model_copy(update=updates)
                )
                readiness = scenario.get_readiness()
            except ValueError as exc:
                readiness = ScenarioReadiness(
                    issues=[
                        ScenarioConfigurationIssue(
                            role="configuration",
                            component=scenario_name,
                            reason=str(exc),
                            remediation="Correct the selected scenario parameters, datasets, or techniques.",
                        )
                    ]
                )
        invalid_roles = {issue.role for issue in issues}
        readiness.bindings = [binding for binding in readiness.bindings if binding.role not in invalid_roles]
        readiness.issues = issues + [issue for issue in readiness.issues if issue.role not in invalid_roles]
        return readiness

    @classmethod
    def resolve_named_roles(
        cls, *, scenario_name: str, request: RunScenarioRequest | ScenarioRunSizeEstimateRequest
    ) -> tuple[list[ScenarioConfigurationIssue], dict[str, Any], PromptTarget | None]:
        """
        Validate all explicit references before resolving any scenario defaults.

        Returns:
            tuple: Issues, invalid request-field replacements, and explicit generation target.
        """
        issues: list[ScenarioConfigurationIssue] = []
        updates: dict[str, Any] = {}
        target = None
        for field, role in (
            ("target_name", "objective_target"),
            ("scoring_target_name", "scoring_target"),
            ("adversarial_target_name", "adversarial_target"),
        ):
            name = getattr(request, field)
            if name is None:
                continue
            try:
                resolved = cls.resolve_target(target_name=name)
                if role == "adversarial_target":
                    validate_default_adversarial_target(resolved)
                    target = resolved
            except ValueError as exc:
                issues.append(
                    ScenarioConfigurationIssue(
                        role=role,
                        component=name,
                        reason=str(exc),
                        remediation="Select an available compatible target.",
                    )
                )
                updates[field] = None
        if request.objective_scorer is not None:
            try:
                cls.resolve_constructor_kwargs(
                    scenario_class=cls.resolve_scenario_class(scenario_name=scenario_name),
                    objective_scorer=request.objective_scorer,
                )
            except ValueError as exc:
                issues.append(
                    ScenarioConfigurationIssue(
                        role="objective_scorer",
                        component=request.objective_scorer.registry_name,
                        reason=str(exc),
                        remediation="Select the current registered scorer and review its identity.",
                    )
                )
                updates["objective_scorer"] = None
        return issues, updates, target

    @staticmethod
    def resolve_constructor_kwargs(
        *, scenario_class: type[Scenario], objective_scorer: RegisteredComponentReference | None
    ) -> dict[str, Any]:
        """
        Resolve a source-pinned scorer for a scenario constructor.

        Returns:
            dict[str, Any]: Constructor arguments, or an empty mapping for scenario defaults.
        """
        if objective_scorer is None:
            return {}
        if not ScenarioRegistry.supports_objective_scorer(scenario_class):
            raise ValueError("This scenario does not support a custom objective scorer")
        scorer = ScorerRegistry.get_registry_singleton().resolve_source(
            name=objective_scorer.registry_name, identifier_hash=objective_scorer.identifier_hash
        )
        if not isinstance(scorer, TrueFalseScorer):
            raise ValueError("The objective scorer must be a true/false scorer")
        return {"objective_scorer": scorer}

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
        scoring_target_name: str | None = None,
        techniques: list[str] | None = None,
        technique_override: TechniqueOverride | None = None,
        datasets: ScenarioDatasetRequest | None = None,
        dataset_config: DatasetAttackConfiguration | None = None,
        include_baseline: bool | None = None,
        max_concurrency: int | None = None,
        max_retries: int | None = None,
        memory_labels: dict[str, str] | None = None,
        scenario_params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Resolve shared launch and estimate fields into scenario parameters.

        Dataset edits pass through unchanged; the scenario applies and validates them.
        An exact selection is supplied as ``dataset_config`` by ``resolve_configuration_async``.
        Its examples must run unchanged under every selected technique that has a factory:
        the shared compatibility checks reject a technique that would skip an example or
        run it only after objective-only adaptation. The factories checked are the ones
        the scenario executes, resolved by the scenario itself with ``scenario_params``
        and the resolved values, so a registered override or run-local edit is checked
        rather than a catalog default it replaces.

        Returns:
            dict[str, Any]: Values accepted by ``Scenario.set_params_from_args``.

        Raises:
            DatasetExampleSelectionError: If a selected technique cannot run an exact example unchanged.
            ValueError: If techniques or dataset overrides are invalid.
        """
        resolved: dict[str, Any] = {}
        if scoring_target_name is not None:
            resolved["scoring_target"] = cls.resolve_target(target_name=scoring_target_name)
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

        if dataset_config is not None:
            resolved["dataset_config"] = dataset_config
        elif datasets is not None:
            resolved["dataset_overrides"] = datasets
        exact_selection = dataset_config if isinstance(dataset_config, ExactExampleDatasetConfiguration) else None
        if not techniques and technique_override is None and exact_selection is None:
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

        if technique_override is not None:
            from pyrit.backend.services.technique_edit_service import TechniqueEditService

            if scenario_class.MAX_CONCRETE_TECHNIQUES != 1 or "local_technique_factory" not in {
                parameter.name for parameter in scenario_class.supported_parameters()
            }:
                raise ValueError("This scenario does not support run-local technique editing")
            selected = introspection_instance._technique_class.resolve(
                resolved.get("scenario_techniques"), default=introspection_instance._technique_class.default()
            )
            if len(selected) != 1 or selected[0].value != technique_override.source_name:
                raise ValueError("A technique edit requires exactly its one selected concrete source technique")
            resolved["local_technique_factory"] = TechniqueEditService.resolve(technique_override)

        if exact_selection is not None:
            # Configure the introspection instance with the parameters the run receives, then
            # ask the scenario which factory it executes for each selected technique.
            introspection_instance.set_params_from_args(args={**(scenario_params or {}), **resolved})
            exact_selection.validate_technique_factories(introspection_instance._get_selected_technique_factories())
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

    @classmethod
    async def resolve_configuration_async(
        cls,
        *,
        scenario_name: str,
        scenario_class: type[Scenario],
        objective_target: Any | None = None,
        scoring_target_name: str | None = None,
        techniques: list[str] | None = None,
        technique_override: TechniqueOverride | None = None,
        datasets: ScenarioDatasetRequest | None = None,
        example_selection: DatasetExampleSelection | None = None,
        include_baseline: bool | None = None,
        max_concurrency: int | None = None,
        max_retries: int | None = None,
        memory_labels: dict[str, str] | None = None,
        memory: MemoryInterface | None = None,
        reject_exact_selection: bool = False,
        scenario_params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Resolve launch and estimate fields, including an exact example selection.

        Named-dataset requests keep today's override behavior. An exact selection replaces
        the population and is rejected by the explicit dataset load route.

        Returns:
            dict[str, Any]: Values accepted by ``Scenario.set_params_from_args``.

        Raises:
            DatasetExampleSelectionError: If the exact selection is stale, missing, or unsupported.
            ValueError: If techniques or dataset overrides are invalid.
        """
        dataset_config = None
        datasets_for_named_mode = datasets
        if example_selection is not None:
            if reject_exact_selection:
                raise DatasetExampleSelectionError(
                    code="conflicting_mode",
                    detail="Exact example selection has nothing to fetch.",
                )
            if selection_conflicts_with_datasets(datasets):
                raise DatasetExampleSelectionError(
                    code="conflicting_mode",
                    detail=(
                        "Exact example selection cannot be combined with dataset sources, filters, limits, or fetch."
                    ),
                )
            if not scenario_class.SUPPORTS_EXACT_EXAMPLE_SELECTION:
                raise DatasetExampleSelectionError(
                    code="unsupported",
                    detail="This scenario does not support an exact example selection.",
                )
            if not scenario_accepts_dataset_config(scenario_class):
                raise DatasetExampleSelectionError(
                    code="unsupported",
                    detail="This scenario does not accept a dataset configuration.",
                )
            resolved_examples = await resolve_dataset_example_selection_async(
                selection=example_selection, memory=memory
            )
            dataset_config = exact_example_dataset_configuration(resolved_examples)
            datasets_for_named_mode = None
        return await asyncio.to_thread(
            cls.resolve_configuration,
            scenario_name=scenario_name,
            scenario_class=scenario_class,
            objective_target=objective_target,
            scoring_target_name=scoring_target_name,
            techniques=techniques,
            technique_override=technique_override,
            datasets=datasets_for_named_mode,
            dataset_config=dataset_config,
            include_baseline=include_baseline,
            max_concurrency=max_concurrency,
            max_retries=max_retries,
            memory_labels=memory_labels,
            scenario_params=scenario_params,
        )
