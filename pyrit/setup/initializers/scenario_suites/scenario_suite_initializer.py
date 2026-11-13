# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Catalog-driven registration of code-owned scenario suites and their presets."""

import asyncio
import copy
import logging
from dataclasses import dataclass, field
from typing import Any, ClassVar
from uuid import NAMESPACE_URL, uuid5

from pydantic import JsonValue, TypeAdapter

from pyrit.models import Parameter, ScenarioPreset, ScenarioSuite, ScenarioSuiteEntry, validate_registry_name
from pyrit.registry import ScenarioPresetRegistry, ScenarioSuiteRegistry
from pyrit.setup.initializers.scenario_suites._suite_recipe import (
    _resolve_suite_arguments,
    _SuiteCatalogValidator,
    _SuiteRecipe,
)
from pyrit.setup.initializers.scenario_suites.horizontal_techniques import HorizontalTechniquesSuite
from pyrit.setup.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScenarioSuiteConfig:
    """A named suite configuration, analogous to a target or converter catalog entry."""

    registry_name: str
    suite_class: type[_SuiteRecipe]
    constructor_args: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ("default",)


class ScenarioSuiteInitializer(PyRITInitializer):
    """
    Register curated scenario suites and their presets from a configurable catalog.

    Select catalog groups with ``tags`` and customize selected entries with ``suite_args``.
    Custom initializers may extend ``CONFIGS`` with builders or named configurations of
    the same builder. The complete batch is prepared before any definition is registered.
    """

    CONFIGS: ClassVar[tuple[ScenarioSuiteConfig, ...]] = (
        ScenarioSuiteConfig(registry_name="horizontal_techniques", suite_class=HorizontalTechniquesSuite),
    )

    @classmethod
    def supported_parameters(cls) -> list[Parameter]:
        """Return catalog selection and explicitly validated nested overrides."""
        return [
            Parameter(
                name="tags",
                description="Catalog groups to register, or ['all'] for every entry.",
                param_type=list[str],
                default=["default"],
            ),
            Parameter(
                name="suite_args",
                description="YAML/Python mapping of selected suite names to constructor overrides.",
                param_type=dict[str, dict[str, JsonValue]],
                default={},
            ),
        ]

    def validate_params(self) -> None:
        """Validate nested arguments and constructors without catalog I/O or registry mutations."""
        self._configured_recipes()

    async def preflight_async(self) -> None:
        """Run opted-in recipe catalog checks without building presets or consulting live registries."""
        for _, recipe in self._configured_recipes():
            if isinstance(recipe, _SuiteCatalogValidator):
                await asyncio.to_thread(recipe.validate_catalogs)

    async def initialize_async(self) -> None:
        """Prepare the complete batch before registering any definitions."""
        configured = self._configured_recipes()
        prepared = await asyncio.to_thread(self._prepare, configured)
        await asyncio.to_thread(self._register, prepared)

    def _selected_configs(self) -> list[ScenarioSuiteConfig]:
        """
        Validate catalog identity and select entries in catalog order.

        Returns:
            list[ScenarioSuiteConfig]: Selected catalog entries.

        Raises:
            ValueError: If names collide or tag selection is invalid or empty.
        """
        names: set[str] = set()
        for config in self.CONFIGS:
            validate_registry_name(config.registry_name)
            if config.registry_name in names:
                raise ValueError(f"Duplicate suite catalog name '{config.registry_name}'.")
            names.add(config.registry_name)
        tags = self.params["tags"]
        if not tags:
            raise ValueError("tags must contain at least one catalog group.")
        if "all" in tags and tags != ["all"]:
            raise ValueError("'all' must be the only tag.")
        unknown = set(tags) - ({tag for config in self.CONFIGS for tag in config.tags} | {"all"})
        if unknown:
            raise ValueError(f"Unknown suite tags: {', '.join(sorted(unknown))}.")
        selected = [config for config in self.CONFIGS if tags == ["all"] or set(config.tags).intersection(tags)]
        if not selected:
            raise ValueError("No scenario suites match the selected tags.")
        return selected

    def _configured_recipes(self) -> list[tuple[ScenarioSuiteConfig, _SuiteRecipe]]:
        """
        Resolve independent constructor arguments and run side-effect-free validation.

        Returns:
            list[tuple[ScenarioSuiteConfig, _SuiteRecipe]]: Configurations and validated recipes.

        Raises:
            ValueError: If overrides are malformed or reference unselected suites.
        """
        super().validate_params()
        overrides = TypeAdapter(dict[str, dict[str, JsonValue]]).validate_python(self.params["suite_args"], strict=True)
        selected = self._selected_configs()
        invalid = overrides.keys() - {config.registry_name for config in selected}
        if invalid:
            raise ValueError(f"suite_args names unknown or unselected suites: {', '.join(sorted(invalid))}.")
        recipes = []
        for config in selected:
            args = copy.deepcopy(config.constructor_args)
            args.update(copy.deepcopy(overrides.get(config.registry_name, {})))
            resolved = _resolve_suite_arguments(suite_class=config.suite_class, args=args)
            recipes.append((config, config.suite_class(**resolved)))
        return recipes

    def _prepare(
        self, configured: list[tuple[ScenarioSuiteConfig, _SuiteRecipe]]
    ) -> list[tuple[ScenarioSuite, list[ScenarioPreset]]]:
        """
        Build all definitions and check batch, registered, and stored conflicts before mutation.

        Args:
            configured (list[tuple[ScenarioSuiteConfig, _SuiteRecipe]]): Validated recipes for this invocation.

        Returns:
            list[tuple[ScenarioSuite, list[ScenarioPreset]]]: Complete registration batch.

        Raises:
            ValueError: If a suite is empty or preset names collide.
        """
        prepared = []
        names: set[str] = set()
        for config, recipe in configured:
            presets = recipe.build_presets(suite_name=config.registry_name)
            if not presets:
                raise ValueError(f"Scenario suite '{config.registry_name}' must contain at least one preset.")
            for preset in presets:
                if preset.name in names:
                    raise ValueError(f"Duplicate suite preset name '{preset.name}'.")
                names.add(preset.name)
            suite = ScenarioSuite(
                name=config.registry_name,
                description=recipe.DESCRIPTION,
                entries=[
                    ScenarioSuiteEntry(
                        entry_id=uuid5(NAMESPACE_URL, f"pyrit:scenario_suite:{config.registry_name}:{preset.name}"),
                        preset_name=preset.name,
                    )
                    for preset in presets
                ],
            )
            prepared.append((suite, presets))
        self._check_conflicts(prepared)
        return prepared

    @staticmethod
    def _check_conflicts(prepared: list[tuple[ScenarioSuite, list[ScenarioPreset]]]) -> None:
        """
        Check registered instances and operator-owned documents, including malformed storage.

        Raises:
            ValueError: If a definition conflicts with registered or stored content.
        """
        preset_registry = ScenarioPresetRegistry.get_registry_singleton()
        suite_registry = ScenarioSuiteRegistry.get_registry_singleton()
        for suite, presets in prepared:
            for preset in presets:
                _is_registered(registered=preset_registry.instances.get(preset.name), definition=preset)
                if preset_registry.get_preset_version(name=preset.name) is not None:
                    stored = preset_registry.get_preset(name=preset.name)
                    if stored is None or stored.origin != "stored" or stored.preset != preset:
                        raise ValueError(f"Scenario preset '{preset.name}' conflicts with a stored document.")
            _is_registered(registered=suite_registry.instances.get(suite.name), definition=suite)
            if suite_registry.get_suite_version(name=suite.name) is not None:
                stored_suite = suite_registry.get_suite(name=suite.name)
                if stored_suite is None or stored_suite.origin != "stored" or stored_suite.suite != suite:
                    raise ValueError(f"Scenario suite '{suite.name}' conflicts with a stored document.")

    @staticmethod
    def _register(prepared: list[tuple[ScenarioSuite, list[ScenarioPreset]]]) -> None:
        """Register the preflighted batch; unexpected storage failures propagate without rollback."""
        preset_registry = ScenarioPresetRegistry.get_registry_singleton()
        suite_registry = ScenarioSuiteRegistry.get_registry_singleton()
        for suite, presets in prepared:
            tags = {"scenario_suite": suite.name}
            for preset in presets:
                if not _is_registered(registered=preset_registry.instances.get(preset.name), definition=preset):
                    preset_registry.register_preset(preset=preset, tags=tags)
            if not _is_registered(registered=suite_registry.instances.get(suite.name), definition=suite):
                suite_registry.register_suite(suite=suite, tags=tags)
            logger.info("Registered scenario suite '%s' with %d preset(s).", suite.name, len(presets))


def _is_registered(
    *, registered: ScenarioPreset | ScenarioSuite | None, definition: ScenarioPreset | ScenarioSuite
) -> bool:
    """
    Determine whether an identical definition is already registered.

    Returns:
        bool: Whether the name already holds this definition.

    Raises:
        ValueError: If the registered definition differs.
    """
    if registered is None:
        return False
    if registered != definition:
        raise ValueError(f"'{definition.name}' is already registered with a different definition.")
    return True
