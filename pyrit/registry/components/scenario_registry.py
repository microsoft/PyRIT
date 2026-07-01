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

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyrit.models import class_name_to_snake_case
from pyrit.models.identifiers.scenario_identifier import ScenarioIdentifier
from pyrit.registry.base import ClassRegistryEntry
from pyrit.registry.discovery import (
    discover_in_package,
    discover_subclasses_in_loaded_modules,
)
from pyrit.registry.registry import Registry

if TYPE_CHECKING:
    from pyrit.models import Parameter
    from pyrit.models.identifiers.component_identifier import ComponentIdentifier
    from pyrit.scenario.core import Scenario

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScenarioMetadata(ClassRegistryEntry):
    """
    Metadata describing a registered Scenario class.

    Use get_class() to get the actual class.
    """

    # The default strategy name (e.g., "single_turn")
    default_strategy: str = field(kw_only=True)

    # All available strategy names for this scenario.
    all_strategies: tuple[str, ...] = field(kw_only=True)

    # Aggregate strategies that combine multiple attack approaches.
    aggregate_strategies: tuple[str, ...] = field(kw_only=True)

    # Default dataset names used by this scenario.
    default_datasets: tuple[str, ...] = field(kw_only=True)

    # Maximum number of items per dataset.
    max_dataset_size: int | None = field(kw_only=True)

    # Scenario-declared custom parameters.
    supported_parameters: tuple[Parameter, ...] = field(kw_only=True, default=())


class ScenarioRegistry(Registry["Scenario", ScenarioMetadata]):
    """
    Registry for discovering and managing available scenario classes.

    This class discovers all Scenario subclasses from:
    1. Built-in scenarios in pyrit.scenario.scenarios module
    2. User-defined scenarios from initialization scripts (set via globals)

    Scenarios are identified by their dotted name (e.g., "garak.encoding", "foundry.red_team_agent").
    """

    def _identifier_type(self) -> type[ComponentIdentifier] | None:
        """Return ``ScenarioIdentifier`` so ``Param.*`` markers drive derivation."""
        return ScenarioIdentifier

    def _metadata_class(self) -> type[ScenarioMetadata]:
        """Return the concrete metadata dataclass this registry builds."""
        return ScenarioMetadata

    def _get_registry_name(self, cls: type[Scenario]) -> str:
        """
        Scenarios are keyed by dotted registry name assigned during discovery.

        User-defined scenarios discovered outside the package fall back to a
        suffix-stripped snake_case class name.

        Args:
            cls (type[Scenario]): The scenario class.

        Returns:
            str: The snake_case registry name.
        """
        return class_name_to_snake_case(cls.__name__, suffix="Scenario")

    def _discover(self) -> None:
        """Discover all built-in scenarios from pyrit.scenario.scenarios module."""
        from pyrit.scenario.core import Scenario

        try:
            import pyrit.scenario.scenarios as scenarios_package

            # Get the path to the scenarios package
            package_file = scenarios_package.__file__
            if package_file is None:
                if hasattr(scenarios_package, "__path__"):
                    package_path = Path(scenarios_package.__path__[0])
                else:
                    logger.error("Cannot determine scenarios package location")
                    return
            else:
                package_path = Path(package_file).parent

            # Discover scenarios using the shared discovery utility
            # Use ``package_name.module_name`` as the registry name
            for registry_name, scenario_class in discover_in_package(
                package_path=package_path,
                package_name="pyrit.scenario.scenarios",
                base_class=Scenario,
                recursive=True,
            ):
                # Skip deprecated alias classes
                doc = (scenario_class.__doc__ or "").strip()
                if doc.startswith("Deprecated alias"):
                    logger.debug(f"Skipping deprecated alias: {scenario_class.__name__}")
                    continue

                # Skip re-exported aliases: if the class was defined in a different
                # module than the one being discovered, it's an alias (e.g.,
                # ContentHarms in content_harms.py is really RapidResponse from
                # rapid_response.py).
                class_module = getattr(scenario_class, "__module__", "")
                if not class_module.endswith(registry_name.replace("/", ".")):
                    # Build the full expected module name for comparison
                    expected_module = f"pyrit.scenario.scenarios.{registry_name.replace('/', '.')}"
                    if class_module != expected_module:
                        logger.debug(
                            f"Skipping alias '{scenario_class.__name__}' in '{registry_name}' "
                            f"(defined in {class_module})"
                        )
                        continue

                # Check for registry key collision
                if registry_name in self._classes:
                    logger.warning(
                        f"Scenario registry name collision: '{registry_name}' "
                        f"conflicts with an already-registered scenario. Original "
                        f"scenario is kept: {self._classes[registry_name].__name__}"
                    )
                    continue

                self.register_class(scenario_class, name=registry_name)
                logger.debug(f"Registered built-in scenario: {registry_name} ({scenario_class.__name__})")

        except Exception as e:
            logger.error(f"Failed to discover built-in scenarios: {e}")

    def discover_user_scenarios(self) -> None:
        """
        Discover user-defined scenarios from global variables.

        After initialization scripts are executed, they may define Scenario subclasses
        and store them in globals. This method searches for such classes.

        User scenarios will override built-in scenarios with the same name.
        """
        from pyrit.scenario.core import Scenario

        try:
            for _, scenario_class in discover_subclasses_in_loaded_modules(base_class=Scenario):
                # Check if this is a user-defined class (not from pyrit.scenario.scenarios)
                if not scenario_class.__module__.startswith("pyrit.scenario.scenarios"):
                    registry_name = class_name_to_snake_case(scenario_class.__name__, suffix="Scenario")
                    self.register_class(scenario_class, name=registry_name)
                    logger.info(f"Registered user-defined scenario: {registry_name} ({scenario_class.__name__})")

        except Exception as e:
            logger.debug(f"Failed to discover user scenarios: {e}")

    def _build_metadata(self, name: str, cls: type[Scenario]) -> ScenarioMetadata:
        """
        Build metadata for a Scenario class.

        Instantiates the scenario with no arguments and reads the strategy/dataset
        configuration off the instance. Every registered scenario MUST be no-arg
        instantiable (defer required-input validation to ``initialize_async`` or
        ``_get_atomic_attacks_async``); otherwise this raises ``TypeError``.

        Args:
            name: The registry name of the scenario.
            cls: The scenario class to describe.

        Returns:
            ScenarioMetadata describing the scenario class.

        Raises:
            TypeError: If ``cls()`` cannot be called with no arguments.
        """
        description = ClassRegistryEntry.description_from_docstring(cls, fallback="No description available")

        supported_parameters = tuple(cls.supported_parameters())

        try:
            instance = cls()  # type: ignore[ty:missing-argument]
        except TypeError as exc:
            raise TypeError(
                f"Scenario {cls.__module__}.{cls.__name__} (registered as "
                f"{name!r}) must be instantiable with no arguments so the registry can introspect "
                f"its strategies and default dataset config. Make all constructor parameters "
                f"optional (defaulting to None) and defer required-input validation to "
                f"initialize_async() or _get_atomic_attacks_async(). Original error: {exc}"
            ) from exc

        strategy_class = instance._strategy_class
        default_strategy_value = instance._default_strategy.value
        all_strategies = tuple(s.value for s in strategy_class.get_all_strategies())
        aggregate_strategies = tuple(s.value for s in strategy_class.get_aggregate_strategies())
        default_datasets = tuple(instance._default_dataset_config.dataset_names)
        max_dataset_size = instance._default_dataset_config.max_dataset_size

        return ScenarioMetadata(
            class_name=cls.__name__,
            class_module=cls.__module__,
            class_description=description,
            registry_name=name,
            default_strategy=default_strategy_value,
            all_strategies=all_strategies,
            aggregate_strategies=aggregate_strategies,
            default_datasets=default_datasets,
            max_dataset_size=max_dataset_size,
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
        2. **set parameters** — the scenario-specific declared parameters (from
           ``supported_parameters()``) are coerced/validated/injected via
           ``Scenario.set_params_from_args``,
        3. **initialize** — the run-resolved common parameters (``objective_target``,
           ``scenario_strategies``, ``dataset_config``, ``max_concurrency``,
           ``max_retries``, ``memory_labels``, ``include_baseline``) are forwarded
           to ``Scenario.initialize_async``.

        Prefer this over manually chaining ``create_instance`` +
        ``set_params_from_args`` + ``initialize_async``.

        Args:
            name (str): The registry name of the scenario (e.g. ``"foundry.red_team_agent"``).
            scenario_params (dict[str, Any] | None): Scenario-specific declared
                parameters to set before initialization. Defaults to an empty mapping.
            scenario_result_id (str | None): Existing scenario-result id to resume,
                or ``None`` to start a fresh run.
            **initialize_kwargs (Any): Run-resolved common parameters forwarded to
                ``Scenario.initialize_async`` (notably ``objective_target``).

        Returns:
            Scenario: The fully initialized scenario, ready for ``run_async``.
        """
        constructor_kwargs: dict[str, Any] = {}
        if scenario_result_id:
            constructor_kwargs["scenario_result_id"] = scenario_result_id

        scenario = self.create_instance(name, **constructor_kwargs)
        scenario.set_params_from_args(args=scenario_params or {})
        await scenario.initialize_async(**initialize_kwargs)
        return scenario
