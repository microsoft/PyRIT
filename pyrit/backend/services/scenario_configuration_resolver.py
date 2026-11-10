# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared scenario launch and estimate configuration resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyrit.registry import (
    AttackTechniqueRegistry,
    SavedEntryUnavailableError,
    ScenarioRegistry,
    ScorerRegistry,
    TargetRegistry,
    TechniqueSelectionUnavailableError,
    UnavailableEntry,
)
from pyrit.scenario.core import resolve_technique_tokens
from pyrit.scenario.core.scenario_target_defaults import validate_default_adversarial_target
from pyrit.score import TrueFalseScorer

if TYPE_CHECKING:
    from pyrit.converter import Converter
    from pyrit.models import ScenarioDatasetRequest
    from pyrit.models.component_spec import RegisteredComponentReference
    from pyrit.models.technique_edit import TechniqueOverride
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario import Scenario


class ScenarioConfigurationResolver:
    """Resolve registry-backed scenario inputs for launch and estimation."""

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

    @staticmethod
    def _refuse_broad_selection_while_techniques_are_held(
        *,
        scenario_class: type[Scenario],
        techniques: list[str] | None,
        technique_override: TechniqueOverride | None,
    ) -> None:
        """
        Refuse choosing techniques by all, default, or a tag while a saved technique couldn't be restored.

        A broken technique's tags may be unknown, so such a selection could quietly leave it out. Naming
        techniques still works, and scenarios whose techniques don't come from the registry aren't affected.

        Raises:
            SavedEntryUnavailableError: If a named technique is held for a saved technique that couldn't be restored.
            TechniqueSelectionUnavailableError: If the selection is broad while a saved technique is held.
        """
        held = raise_if_a_named_technique_is_held(techniques)
        if not held or "scenario_techniques" not in {p.name for p in scenario_class.supported_parameters()}:
            return
        selectors = [token.partition(":")[0] for token in techniques or []]
        if not selectors and technique_override is not None:
            return
        try:
            technique_class = scenario_class()._technique_class  # type: ignore[ty:missing-argument]
        except Exception:
            technique_class = None
        if technique_class is not None and getattr(technique_class, "_selection_catalog", None) is None:
            return
        aggregates = {"all", "default"}
        if technique_class is not None:
            aggregates |= {tag.casefold() for tag in technique_class.get_aggregate_tags()}
        if not selectors or any(selector.casefold() in aggregates for selector in selectors):
            raise TechniqueSelectionUnavailableError(names=[entry.name for entry in held if entry.name is not None])

    @classmethod
    def resolve_configuration(
        cls,
        *,
        scenario_name: str,
        scenario_class: type[Scenario],
        objective_target: Any | None = None,
        techniques: list[str] | None = None,
        technique_override: TechniqueOverride | None = None,
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
        cls._refuse_broad_selection_while_techniques_are_held(
            scenario_class=scenario_class, techniques=techniques, technique_override=technique_override
        )
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
        if not techniques and technique_override is None:
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


def raise_if_a_named_technique_is_held(techniques: list[str] | None) -> list[UnavailableEntry]:
    """
    Refuse technique tokens that name a saved technique that couldn't be restored, in any letter case.

    Args:
        techniques (list[str] | None): Technique tokens, each optionally followed by ``:`` and converters.

    Returns:
        list[UnavailableEntry]: Every held saved technique, and a held folder of them.

    Raises:
        SavedEntryUnavailableError: If a token names a held technique.
    """
    held = AttackTechniqueRegistry.get_registry_singleton().instances.get_unavailable_entries()
    held_by_name = {entry.name.casefold(): entry for entry in held if entry.name is not None}
    for token in techniques or []:
        name = token.partition(":")[0]
        if (entry := held_by_name.get(name.casefold())) is not None:
            raise SavedEntryUnavailableError(entry=entry, name=name)
    return held
