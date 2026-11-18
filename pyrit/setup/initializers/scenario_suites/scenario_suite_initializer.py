# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Registration of PyRIT's built-in scenario suites and their presets."""

import asyncio
import logging
from collections.abc import Callable
from typing import ClassVar

from pyrit.models import ScenarioPreset, ScenarioSuite
from pyrit.registry import ScenarioPresetRegistry, ScenarioSuiteRegistry
from pyrit.setup.initializers.scenario_suites.horizontal_techniques import HorizontalTechniquesSuite
from pyrit.setup.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)


class ScenarioSuiteInitializer(PyRITInitializer):
    """
    Register PyRIT's built-in scenario suites and their presets.

    Each suite is a fixed snapshot of presets with explicit techniques, templates, and
    languages. To run a variation, copy a preset in the GUI and edit the copy. Custom
    initializers may override ``SUITE_BUILDERS``. A stored document with the same name
    shadows the registered definition.
    """

    SUITE_BUILDERS: ClassVar[tuple[Callable[[], tuple[ScenarioSuite, list[ScenarioPreset]]], ...]] = (
        HorizontalTechniquesSuite.build,
    )

    async def initialize_async(self) -> None:
        """Build every suite, then register the definitions."""
        await asyncio.to_thread(self._register)

    def _register(self) -> None:
        """Build and validate the complete batch, then register definitions that are not already registered."""
        prepared = [build() for build in self.SUITE_BUILDERS]
        _validate_batch(prepared)
        preset_registry = ScenarioPresetRegistry.get_registry_singleton()
        suite_registry = ScenarioSuiteRegistry.get_registry_singleton()
        pending = []
        for suite, presets in prepared:
            new_presets = [
                preset
                for preset in presets
                if not _is_registered(registered=preset_registry.instances.get(preset.name), definition=preset)
            ]
            new_suite = not _is_registered(registered=suite_registry.instances.get(suite.name), definition=suite)
            pending.append((suite, new_presets, new_suite))
        for suite, presets, register_suite in pending:
            tags = {"scenario_suite": suite.name}
            for preset in presets:
                preset_registry.register_preset(preset=preset, tags=tags)
            if register_suite:
                suite_registry.register_suite(suite=suite, tags=tags)
            logger.info("Registered scenario suite '%s'.", suite.name)


def _validate_batch(prepared: list[tuple[ScenarioSuite, list[ScenarioPreset]]]) -> None:
    """
    Reject empty suites and duplicate suite or preset names before anything is registered.

    Args:
        prepared (list[tuple[ScenarioSuite, list[ScenarioPreset]]]): Built suites and their presets.

    Raises:
        ValueError: If a suite is empty or names collide.
    """
    suite_names: set[str] = set()
    preset_names: set[str] = set()
    for suite, presets in prepared:
        if suite.name in suite_names:
            raise ValueError(f"Duplicate scenario suite name '{suite.name}'.")
        suite_names.add(suite.name)
        if not presets or not suite.entries:
            raise ValueError(f"Scenario suite '{suite.name}' must contain at least one preset.")
        for preset in presets:
            if preset.name in preset_names:
                raise ValueError(f"Duplicate suite preset name '{preset.name}'.")
            preset_names.add(preset.name)


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
