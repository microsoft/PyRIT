# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Attack technique registry for PyRIT.

A registry for ``AttackTechniqueFactory`` instances that scenarios and
initializers register and later retrieve. Like ``ConverterRegistry`` it is a
``Registry`` whose pre-configured instances live under the ``instances``
property; unlike converters, its buildable class catalog is intentionally empty
for now — the factory still owns its own construction, and the catalog is lit up
later when the factory is decoupled into a buildable component.

Scenarios and initializers register self-describing factories (via
``register_from_factories``), retrieve them with ``get_factories`` /
``get_factories_or_raise``, filter them in-place by factory properties (e.g.
``factory.uses_adversarial`` or strategy tags), and call ``factory.create()``
with the scenario's objective target and scorer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit.registry.instance_registry import DefaultInstanceRegistry, InstanceRegistry
from pyrit.registry.registry import Registry
from pyrit.registry.registry_metadata import RegistryMetadata

if TYPE_CHECKING:
    from pyrit.registry.tag_query import TagQuery
    from pyrit.scenario.core.attack_technique_factory import (
        AttackTechniqueFactory,
        ScorerOverridePolicy,
    )

logger = logging.getLogger(__name__)


def _attack_technique_factory_type() -> type[AttackTechniqueFactory]:
    """
    Return the ``AttackTechniqueFactory`` class, importing it lazily.

    Used as the ``instance_type`` for the registry's ``instances`` container so a
    non-factory cannot be registered, without importing the factory module (which
    pulls in the executor/attack stack) at registry import time.

    Returns:
        type[AttackTechniqueFactory]: The ``AttackTechniqueFactory`` class.
    """
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory

    return AttackTechniqueFactory


@dataclass(frozen=True)
class AttackTechniqueMetadata(RegistryMetadata):
    """
    Metadata describing a registered attack-technique class.

    Placeholder for the buildable catalog, which is intentionally empty until the
    factory is decoupled into a buildable component. It carries only the common
    ``RegistryMetadata`` fields today; technique-specific fields are added when
    the catalog is lit up.
    """


class AttackTechniqueRegistry(Registry["AttackTechniqueFactory", AttackTechniqueMetadata]):
    """
    Registry that holds reusable ``AttackTechniqueFactory`` instances.

    Scenarios and initializers register self-describing
    ``AttackTechniqueFactory`` instances; scenarios retrieve them via
    ``get_factories`` / ``get_factories_or_raise`` and call ``factory.create()``
    with the scenario's objective target and scorer.

    It is a ``Registry``: pre-configured factories live under the ``instances``
    property (``register``, ``get``, ``get_all_instances``, ``get_by_tag``, …),
    a ``DefaultInstanceRegistry``. The buildable class catalog is intentionally
    empty for now — the factory still owns construction — so ``_discover``
    registers no classes.
    """

    def __init__(self, *, lazy_discovery: bool = True) -> None:
        """
        Initialize the registry.

        Args:
            lazy_discovery (bool): If True, class discovery is deferred until first
                access. If False, discovery runs immediately. The buildable catalog
                is empty either way; the flag is accepted for parity with other
                registries.
        """
        from pyrit.scenario.core.attack_technique_factory import ScorerOverridePolicy

        super().__init__(lazy_discovery=lazy_discovery)
        self.instances: InstanceRegistry[AttackTechniqueFactory] = DefaultInstanceRegistry(
            instance_type=_attack_technique_factory_type
        )
        self._scorer_override_policy = ScorerOverridePolicy.WARN

    def _discover(self) -> None:
        """Register no classes: the factory owns construction; the catalog is lit up later."""

    def _metadata_class(self) -> type[AttackTechniqueMetadata]:
        """Return ``AttackTechniqueMetadata``; unused while the buildable catalog is empty."""
        return AttackTechniqueMetadata

    def register_technique(
        self,
        *,
        name: str,
        factory: AttackTechniqueFactory,
        tags: dict[str, str] | list[str] | None = None,
    ) -> None:
        """
        Register an attack technique factory.

        Args:
            name (str): The registry name for this technique.
            factory (AttackTechniqueFactory): The factory that produces attack techniques.
            tags (dict[str, str] | list[str] | None): Optional tags for categorisation.
                Accepts a ``dict[str, str]`` or a ``list[str]`` (each string becomes a
                key with value ``""``).
        """
        self.instances.register(factory, name=name, tags=tags)
        logger.debug(f"Registered attack technique factory: {name} ({factory.attack_class.__name__})")

    def get_factories(self) -> dict[str, AttackTechniqueFactory]:
        """
        Return all registered factories as a name→factory dict.

        Callers filter the result in-place using factory properties (e.g.
        ``factory.uses_adversarial`` or ``factory.strategy_tags``).

        Returns:
            dict[str, AttackTechniqueFactory]: Mapping of technique name to factory.
        """
        return {entry.name: entry.instance for entry in self.instances.get_all_instances()}

    def get_factories_or_raise(self) -> dict[str, AttackTechniqueFactory]:
        """
        Return all registered factories, raising if the registry is empty.

        Use this from any code path that needs the registry to be populated
        (scenario strategy builders, scenario initialization) so an empty
        registry surfaces a single, descriptive error instead of silently
        producing empty strategy enums or empty attack lists.

        Returns:
            dict[str, AttackTechniqueFactory]: Mapping of technique name to factory.

        Raises:
            RuntimeError: If the registry has no registered factories.
        """
        factories = self.get_factories()
        if not factories:
            raise RuntimeError(
                "AttackTechniqueRegistry is empty. Register attack technique factories before "
                "executing scenarios — for example by running the default "
                "TechniqueInitializer "
                "(pyrit.setup.initializers.techniques), "
                "running another initializer that calls "
                "AttackTechniqueRegistry.register_from_factories(...), or registering "
                "factories directly via AttackTechniqueRegistry.get_registry_singleton()."
            )
        return factories

    @property
    def scorer_override_policy(self) -> ScorerOverridePolicy:
        """The policy applied when a scenario scorer is incompatible with an attack's annotation."""
        return self._scorer_override_policy

    @staticmethod
    def build_strategy_class_from_factories(
        *,
        class_name: str,
        factories: list[AttackTechniqueFactory],
        aggregate_tags: dict[str, TagQuery],
    ) -> type:
        """
        Build a ``ScenarioStrategy`` enum subclass dynamically from technique factories.

        Creates an enum class with:
        - An ``ALL`` aggregate member (always included).
        - Additional aggregate members from ``aggregate_tags`` keys.
        - One technique member per factory, with tags from the factory.

        Each aggregate maps to a ``TagQuery`` that determines which
        technique factories belong to it.

        Args:
            class_name (str): Name for the generated enum class.
            factories (list[AttackTechniqueFactory]): Technique factories to include
                as enum members.
            aggregate_tags (dict[str, TagQuery]): Maps aggregate member names to a
                ``TagQuery`` that selects which techniques belong to the aggregate.
                An ``ALL`` aggregate (expanding to all techniques) is always added.

        Returns:
            type: A ``ScenarioStrategy`` subclass with the generated members.
        """
        from pyrit.scenario import ScenarioStrategy

        all_aggregate_tag_names = {"all"} | set(aggregate_tags.keys())

        members: dict[str, tuple[str, set[str]]] = {}

        # Aggregate members first (ALL is always present)
        members["ALL"] = ("all", {"all"})
        for agg_name in aggregate_tags:
            members[agg_name.upper()] = (agg_name, {agg_name})

        # Technique members from factories — assign aggregate tags based on TagQuery matching
        for factory in factories:
            factory_tags = set(factory.strategy_tags)
            matched_agg_tags = {agg_name for agg_name, query in aggregate_tags.items() if query.matches(factory_tags)}
            members[factory.name] = (factory.name, factory_tags | matched_agg_tags)

        # Build the enum class dynamically
        strategy_cls = ScenarioStrategy(class_name, members)

        # Override get_aggregate_tags on the generated class
        @classmethod
        def _get_aggregate_tags(cls: type) -> set[str]:
            return set(all_aggregate_tag_names)

        strategy_cls.get_aggregate_tags = _get_aggregate_tags  # type: ignore[ty:invalid-assignment]

        return strategy_cls  # type: ignore[ty:invalid-return-type]

    def register_from_factories(
        self,
        factories: list[AttackTechniqueFactory],
    ) -> None:
        """
        Register a list of factories under their ``name``.

        Per-name idempotent: existing entries are not overwritten.

        Args:
            factories (list[AttackTechniqueFactory]): Self-describing factories to
                register. Each factory's ``name`` and ``strategy_tags`` properties are
                used directly.
        """
        for factory in factories:
            if factory.name not in self.instances:
                tags: dict[str, str] = dict.fromkeys(factory.strategy_tags, "")
                self.register_technique(
                    name=factory.name,
                    factory=factory,
                    tags=tags,
                )

        logger.debug(
            "Technique registration complete (%d total in registry)",
            len(self.instances),
        )
