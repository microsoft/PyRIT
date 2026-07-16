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
``factory.uses_adversarial`` or technique tags), and call ``factory.create()``
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
        ``factory.uses_adversarial`` or ``factory.technique_tags``).

        Returns:
            dict[str, AttackTechniqueFactory]: Mapping of technique name to factory.
        """
        return {entry.name: entry.instance for entry in self.instances.get_all_instances()}

    def get_factories_or_raise(self) -> dict[str, AttackTechniqueFactory]:
        """
        Return all registered factories, raising if the registry is empty.

        Use this from any code path that needs the registry to be populated
        (scenario technique builders, scenario initialization) so an empty
        registry surfaces a single, descriptive error instead of silently
        producing empty technique enums or empty attack lists.

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
    def build_technique_class_from_factories(
        *,
        class_name: str,
        factories: list[AttackTechniqueFactory],
        aggregate_tags: dict[str, TagQuery],
        available: TagQuery | None = None,
        default: TagQuery | None = None,
        default_technique_names: set[str] | None = None,
    ) -> type:
        """
        Build a ``ScenarioTechnique`` enum subclass dynamically from technique factories.

        Creates an enum class with:
        - An ``ALL`` aggregate member (always included).
        - A ``DEFAULT`` aggregate member when a default selection is provided.
        - Additional aggregate members from ``aggregate_tags`` keys.
        - One technique member per *available* factory, with tags from the factory.

        The three selection roles are all expressed the same way — as tag queries
        over ``factories`` — and relate as strict subsets:

        - **available** (the pool): ``available`` filters ``factories`` to the
            techniques this scenario exposes. When ``None`` the whole ``factories``
            list is the pool (back-compatible).
        - **aggregates**: named ``TagQuery`` presets (e.g. ``single_turn``); each is
            evaluated only over the pool, so every aggregate is a subset of available.
        - **default**: what runs when the caller selects nothing. Given as a
            ``TagQuery`` (``default``) and/or explicit ``default_technique_names``;
            both are intersected with the pool, so ``DEFAULT`` is always a subset of
            available.

        ``default`` is deliberately **not** an intrinsic technique tag: what runs by
        default differs per scenario. A scenario selects its default set via a query
        or by name so the same technique can be default for one scenario and not
        another, without a catalog-wide tag.

        Args:
            class_name (str): Name for the generated enum class.
            factories (list[AttackTechniqueFactory]): Candidate technique factories.
                Filtered by ``available`` to form the pool of enum members.
            aggregate_tags (dict[str, TagQuery]): Maps aggregate member names to a
                ``TagQuery`` that selects which pool techniques belong to the aggregate.
                An ``ALL`` aggregate (expanding to all pool techniques) is always added.
            available (TagQuery | None): Query selecting which of ``factories`` are
                available for this scenario (the pool). ``None`` means all of them.
            default (TagQuery | None): Query selecting the pool techniques that form
                the ``DEFAULT`` aggregate. Combined (union) with
                ``default_technique_names``.
            default_technique_names (set[str] | None): Names of pool techniques that
                form this scenario's ``DEFAULT`` aggregate. Combined (union) with
                ``default``. Names not present in the pool are ignored, so a scenario
                can list its intended default set even when some of those techniques
                are filtered out of its pool. When the combined default selection is
                empty, no ``DEFAULT`` aggregate is generated.

        Returns:
            type: A ``ScenarioTechnique`` subclass with the generated members.
        """
        from pyrit.scenario import ScenarioTechnique

        # available (the pool): filter the candidate factories by the availability query.
        pool = available.filter(factories) if available is not None else list(factories)

        # default: resolve from an explicit name set and/or a query over the pool. The
        # DEFAULT aggregate exists whenever a default was requested; its membership is
        # limited to the pool below (only pool factories are iterated), so DEFAULT is
        # always a subset of available.
        default_names: set[str] = set(default_technique_names or set())
        if default is not None:
            default_names |= {f.name for f in pool if default.matches(set(f.technique_tags))}

        all_aggregate_tag_names = {"all"} | set(aggregate_tags.keys())
        if default_names:
            all_aggregate_tag_names.add("default")

        members: dict[str, tuple[str, set[str]]] = {}

        # Aggregate members first (ALL is always present)
        members["ALL"] = ("all", {"all"})
        if default_names:
            members["DEFAULT"] = ("default", {"default"})
        for agg_name in aggregate_tags:
            members[agg_name.upper()] = (agg_name, {agg_name})

        # Technique members from the pool — assign aggregate tags based on TagQuery matching
        for factory in pool:
            factory_tags = set(factory.technique_tags)
            matched_agg_tags = {agg_name for agg_name, query in aggregate_tags.items() if query.matches(factory_tags)}
            if factory.name in default_names:
                matched_agg_tags.add("default")
            members[factory.name] = (factory.name, factory_tags | matched_agg_tags)

        # Build the enum class dynamically
        technique_cls = ScenarioTechnique(class_name, members)

        # Override get_aggregate_tags on the generated class
        @classmethod
        def _get_aggregate_tags(cls: type) -> set[str]:
            return set(all_aggregate_tag_names)

        technique_cls.get_aggregate_tags = _get_aggregate_tags  # type: ignore[ty:invalid-assignment]

        return technique_cls  # type: ignore[ty:invalid-return-type]

    def register_from_factories(
        self,
        factories: list[AttackTechniqueFactory],
    ) -> None:
        """
        Register a list of factories under their ``name``.

        Per-name idempotent: existing entries are not overwritten.

        Args:
            factories (list[AttackTechniqueFactory]): Self-describing factories to
                register. Each factory's ``name`` and ``technique_tags`` properties are
                used directly.
        """
        for factory in factories:
            if factory.name not in self.instances:
                tags: dict[str, str] = dict.fromkeys(factory.technique_tags, "")
                self.register_technique(
                    name=factory.name,
                    factory=factory,
                    tags=tags,
                )

        logger.debug(
            "Technique registration complete (%d total in registry)",
            len(self.instances),
        )
