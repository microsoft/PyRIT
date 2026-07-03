# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Technique initializer.

Aggregates the per-group technique catalogs (``core``, ``extra``) into a flat
list of self-describing ``AttackTechniqueFactory`` instances and registers the
selected groups into the singleton ``AttackTechniqueRegistry`` via
``TechniqueInitializer``.

Each group module (e.g. ``core.py``) exposes ``get_technique_factories()``;
``build_technique_factories`` injects the group name as a strategy tag so
techniques are selectable as a group (e.g. the ``core`` aggregate).

Per-name registration is idempotent: pre-existing entries in the registry are
not overwritten.
"""

import logging
from enum import Enum

from pyrit.models.parameter import Parameter
from pyrit.registry.components.attack_technique_registry import (
    AttackTechniqueRegistry,
)
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.setup.initializers.techniques import core, extra
from pyrit.setup.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)


class TechniqueInitializerTags(str, Enum):
    """Technique groups selectable by TechniqueInitializer."""

    CORE = "core"
    EXTRA = "extra"
    ALL = "all"


_GROUP_FACTORY_BUILDERS = {
    TechniqueInitializerTags.CORE.value: core.get_technique_factories,
    TechniqueInitializerTags.EXTRA.value: extra.get_technique_factories,
}


def build_technique_factories(*, groups: list[str] | None = None) -> list[AttackTechniqueFactory]:
    """
    Build the technique factories for the requested groups.

    Each group's factories get the group name injected as a strategy tag (e.g.
    every ``core`` technique gains the ``core`` tag). When ``groups`` is None,
    every group is included — used by consumers that need the full catalog
    regardless of registry state.

    Args:
        groups: Group names to include (e.g. ``["core"]``). Defaults to all groups.

    Returns:
        list[AttackTechniqueFactory]: The factories for the selected groups.

    Raises:
        ValueError: If a requested group is unknown.
    """
    selected = groups if groups else list(_GROUP_FACTORY_BUILDERS.keys())

    factories: list[AttackTechniqueFactory] = []
    for group in selected:
        builder = _GROUP_FACTORY_BUILDERS.get(group)
        if builder is None:
            raise ValueError(
                f"Unknown technique group '{group}'. Available groups: {', '.join(sorted(_GROUP_FACTORY_BUILDERS))}."
            )
        group_factories = builder()
        for factory in group_factories:
            factory.add_strategy_tags(group)
        factories.extend(group_factories)

    return factories


class TechniqueInitializer(PyRITInitializer):
    """
    Register scenario attack technique factories into the AttackTechniqueRegistry.

    By default only the ``core`` group is registered. Pass ``tags`` to select
    groups (``core``, ``extra``, or ``all``). Registration is per-name
    idempotent: pre-existing entries in ``AttackTechniqueRegistry`` are not
    overwritten.
    """

    @property
    def supported_parameters(self) -> list[Parameter]:
        """The list of parameters this initializer accepts."""
        return [
            Parameter(
                name="tags",
                description="Technique groups to register (e.g., ['core'], ['core', 'extra'], or ['all'])",
                default=[TechniqueInitializerTags.CORE.value],
            ),
        ]

    @property
    def required_env_vars(self) -> list[str]:
        """The list of required environment variables."""
        return []

    async def initialize_async(self) -> None:
        """Build the selected technique factories and register them into the singleton registry."""
        tags = self.params.get("tags", [TechniqueInitializerTags.CORE.value])
        if TechniqueInitializerTags.ALL.value in tags:
            tags = [TechniqueInitializerTags.CORE.value, TechniqueInitializerTags.EXTRA.value]

        factories = build_technique_factories(groups=tags)

        registry = AttackTechniqueRegistry.get_registry_singleton()
        registry.register_from_factories(factories)

        registered_names = [f.name for f in factories if f.name in registry]
        logger.info(
            "Registered %d scenario technique factory(ies): %s",
            len(registered_names),
            ", ".join(registered_names),
        )
