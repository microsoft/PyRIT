# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario registry for discovering and managing PyRIT scenarios.

A ``Registry`` for ``Scenario`` classes that discovers all available subclasses
from the ``pyrit.scenario.scenarios`` package and from user-defined initialization
scripts. Like the other component registries it is a unified ``Registry``: it owns
a validated class catalog and builds instances via ``create_instance``. Its
buildable classes are keyed by **dotted registry name** (e.g. ``garak.encoding``)
rather than by class name, so ``_discover``/``_get_registry_name`` are overridden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pyrit.models import class_name_to_snake_case
from pyrit.models.identifiers.scenario_identifier import ScenarioIdentifier
from pyrit.registry.registry import ParamBagRegistry
from pyrit.registry.registry_metadata import RegistryMetadata

if TYPE_CHECKING:
    from types import ModuleType

    from pyrit.models import Parameter
    from pyrit.models.identifiers.component_identifier import ComponentIdentifier
    from pyrit.scenario.core import Scenario


@dataclass(frozen=True)
class ScenarioMetadata(RegistryMetadata):
    """
    Metadata describing a registered Scenario class.

    Use get_class() to get the actual class.
    """

    # The default technique name (e.g., "single_turn")
    default_technique: str = field(kw_only=True)

    # All available technique names for this scenario.
    all_techniques: tuple[str, ...] = field(kw_only=True)

    # Aggregate techniques that combine multiple attack approaches.
    aggregate_techniques: tuple[str, ...] = field(kw_only=True)

    # Default dataset names used by this scenario.
    default_datasets: tuple[str, ...] = field(kw_only=True)

    # Scenario-declared custom parameters.
    supported_parameters: tuple[Parameter, ...] = field(kw_only=True, default=())


class ScenarioRegistry(ParamBagRegistry["Scenario", ScenarioMetadata]):
    """
    Registry for discovering and managing available scenario classes.

    Discovers every concrete ``Scenario`` subclass under ``pyrit.scenario.scenarios``
    via the unified ``Registry`` base (recursive subclass enumeration). Unlike the
    component registries, scenarios are keyed by their **dotted module path** (e.g.
    ``"garak.encoding"``, ``"foundry.red_team_agent"``) rather than class name, so
    only ``_get_registry_name`` and ``_build_metadata`` are customized.
    """

    _DISCOVERY_PACKAGE = "pyrit.scenario.scenarios"

    def _identifier_type(self) -> type[ComponentIdentifier] | None:
        """Return ``ScenarioIdentifier`` so ``Param.*`` markers drive derivation."""
        return ScenarioIdentifier

    def _metadata_class(self) -> type[ScenarioMetadata]:
        """Return the concrete metadata dataclass this registry builds."""
        return ScenarioMetadata

    def _base_type(self) -> type[Scenario]:
        """Return the ``Scenario`` base class, imported lazily."""
        from pyrit.scenario.core import Scenario

        return Scenario

    def _discovery_package(self) -> ModuleType:
        """Return the ``pyrit.scenario.scenarios`` package scanned for scenario classes."""
        import pyrit.scenario.scenarios as scenarios_package

        return scenarios_package

    def _get_registry_name(self, cls: type[Scenario]) -> str:
        """
        Key scenarios by their dotted module path (e.g. ``"airt.rapid_response"``).

        The path is the scenario module's location relative to
        ``pyrit.scenario.scenarios``. Scenarios discovered outside that package
        (e.g. user-defined ones) fall back to a suffix-stripped snake_case class name.

        Args:
            cls (type[Scenario]): The scenario class.

        Returns:
            str: The dotted registry name.
        """
        module = cls.__module__ or ""
        prefix = f"{self._DISCOVERY_PACKAGE}."
        if module.startswith(prefix):
            relative = module[len(prefix) :]
            if relative:
                return relative
        return class_name_to_snake_case(cls.__name__, suffix="Scenario")

    def _build_metadata(self, name: str, cls: type[Scenario]) -> ScenarioMetadata:
        """
        Build metadata for a Scenario class.

        Instantiates the scenario with no arguments and reads the technique/dataset
        configuration off the instance. Every registered scenario MUST be no-arg
        instantiable (defer required-input validation to ``initialize_async`` or
        ``_build_atomic_attacks_async``); otherwise this raises ``TypeError``.

        Args:
            name: The registry name of the scenario.
            cls: The scenario class to describe.

        Returns:
            ScenarioMetadata describing the scenario class.

        Raises:
            TypeError: If ``cls()`` cannot be called with no arguments.
        """
        description = RegistryMetadata.description_from_docstring(cls, fallback="No description available")

        supported_parameters = tuple(cls.supported_parameters())

        try:
            instance = cls()  # type: ignore[ty:missing-argument]
        except TypeError as exc:
            raise TypeError(
                f"Scenario {cls.__module__}.{cls.__name__} (registered as "
                f"{name!r}) must be instantiable with no arguments so the registry can introspect "
                f"its techniques and default dataset config. Make all constructor parameters "
                f"optional (defaulting to None) and defer required-input validation to "
                f"initialize_async() or _build_atomic_attacks_async(). Original error: {exc}"
            ) from exc

        technique_class = instance._technique_class
        default_technique_value = instance._default_technique.value
        all_techniques = tuple(s.value for s in technique_class.get_all_techniques())
        aggregate_techniques = tuple(s.value for s in technique_class.get_aggregate_techniques())
        default_datasets = tuple(instance._default_dataset_config.dataset_names)

        return ScenarioMetadata(
            class_name=cls.__name__,
            class_module=cls.__module__,
            class_description=description,
            registry_name=name,
            default_technique=default_technique_value,
            all_techniques=all_techniques,
            aggregate_techniques=aggregate_techniques,
            default_datasets=default_datasets,
            supported_parameters=supported_parameters,
        )

    async def create_and_initialize_async(
        self,
        name: str,
        *,
        scenario_params: dict[str, Any] | None = None,
        scenario_result_id: str | None = None,
        **initialize_kwargs: Any,
    ) -> Scenario:
        """
        Build, parameterize, and initialize a scenario in one call.

        This is the canonical entry point for producing a run-ready ``Scenario``:
        the registry — not the caller — owns the full lifecycle.

        1. **create** the scenario via ``create_instance`` (seeding
           ``scenario_result_id`` when resuming an existing run),
        2. **set parameters** — the scenario-specific declared parameters
           (``scenario_params``) and the common run-resolved parameters
           (``initialize_kwargs`` — ``objective_target``, ``scenario_techniques``,
           ``dataset_config``, ``max_concurrency``, ``max_retries``,
           ``memory_labels``, ``include_baseline``) are merged into a single
           ``Scenario.set_params_from_args`` call, so every value flows through the
           one coerce/validate/inject-defaults path,
        3. **initialize** — ``Scenario.initialize_async()`` is called with no
           arguments; it reads every input from the now-populated bag.

        Prefer this over manually chaining ``create_instance`` +
        ``set_params_from_args`` + ``initialize_async``.

        Args:
            name (str): The registry name of the scenario (e.g. ``"foundry.red_team_agent"``).
            scenario_params (dict[str, Any] | None): Scenario-specific declared
                parameters to set before initialization. Defaults to an empty mapping.
            scenario_result_id (str | None): Existing scenario-result id to resume,
                or ``None`` to start a fresh run.
            **initialize_kwargs (Any): Common run-resolved parameters merged into the
                param bag (notably ``objective_target``).

        Returns:
            Scenario: The fully initialized scenario, ready for ``run_async``.
        """
        constructor_kwargs: dict[str, Any] = {}
        if scenario_result_id:
            constructor_kwargs["scenario_result_id"] = scenario_result_id

        merged_args = {**(scenario_params or {}), **initialize_kwargs}
        scenario = self._create_and_configure(name, params=merged_args, constructor_kwargs=constructor_kwargs)
        await scenario.initialize_async()
        return scenario
