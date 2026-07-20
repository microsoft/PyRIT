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


def _validate_generated_member_collisions(
    *,
    class_name: str,
    factories: list[AttackTechniqueFactory],
    aggregate_tags: set[str],
) -> None:
    """
    Validate that generated enum member names and values are unambiguous.

    Args:
        class_name (str): Name of the enum class being generated.
        factories (list[AttackTechniqueFactory]): Technique factories that become enum members.
        aggregate_tags (set[str]): Catalog tags that become aggregate members.

    Raises:
        ValueError: If a factory or aggregate would collide with a reserved or generated member.
    """
    member_sources = {"ALL": "reserved aggregate 'all'", "DEFAULT": "reserved aggregate 'default'"}
    value_sources = {"all": "reserved aggregate 'all'", "default": "reserved aggregate 'default'"}

    def _reserve(*, member_name: str, member_value: str, source: str) -> None:
        if existing := member_sources.get(member_name):
            raise ValueError(
                f"Cannot build {class_name}: {source} maps to enum member name {member_name!r}, "
                f"already used by {existing}. Rename the tag or factory."
            )
        if existing := value_sources.get(member_value):
            raise ValueError(
                f"Cannot build {class_name}: {source} maps to enum value {member_value!r}, "
                f"already used by {existing}. Rename the tag or factory."
            )
        member_sources[member_name] = source
        value_sources[member_value] = source

    for tag in sorted(aggregate_tags):
        _reserve(member_name=tag.upper(), member_value=tag, source=f"aggregate tag {tag!r}")
    for factory in factories:
        _reserve(member_name=factory.name, member_value=factory.name, source=f"technique factory {factory.name!r}")


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
        default_tags: set[str] | None = None,
        default_names: set[str] | None = None,
    ) -> type:
        """
        Build a ``ScenarioTechnique`` enum subclass dynamically from technique factories.

        Creates an enum class with:
        - An ``ALL`` aggregate member (always included).
        - A ``DEFAULT`` aggregate member when a default selection is provided and at
            least one pool technique matches it.
        - An aggregate member for every catalog tag present in the pool, so tags and
            aggregates are synonymous: selecting a tag (e.g. ``core`` or a custom
            ``airt_internal``) expands to every technique carrying it.
        - One technique member per factory, with tags from the factory.

        The catalog's *default* — what runs when the caller selects nothing — is defined
        by exactly one of ``default_tags`` or ``default_names`` (or neither). Both build
        the synthetic ``DEFAULT`` aggregate and are recorded on the class, returned by
        ``ScenarioTechnique.default()``. When neither is given, or the chosen set matches
        no pool technique (e.g. a custom initializer registers no ``light``-tagged
        factory), the default falls back to ``ALL``. The default is chosen per-scenario,
        so the same technique can be the default for one scenario and not another.

        Args:
            class_name (str): Name for the generated enum class.
            factories (list[AttackTechniqueFactory]): The technique factories that form
                this scenario's pool of enum members. Callers pre-filter this list to
                shape the pool.
            default_tags (set[str] | None): Tags whose union defines the scenario's
                ``DEFAULT`` aggregate — every pool technique carrying any of these tags is
                the default (e.g. ``{"light"}``). Mutually exclusive with ``default_names``.
            default_names (set[str] | None): Exact technique names that form the
                scenario's ``DEFAULT`` aggregate. Names not present in the pool are
                ignored, so a scenario can list its intended default set even when some of
                those techniques are filtered out. Mutually exclusive with ``default_tags``.

        Returns:
            type: A ``ScenarioTechnique`` subclass with the generated members.

        Raises:
            ValueError: If both ``default_tags`` and ``default_names`` are provided, or if generated
                enum member names or values collide.
        """
        from pyrit.scenario import ScenarioTechnique

        if default_tags and default_names:
            raise ValueError("Provide at most one of default_tags or default_names, not both.")

        pool = list(factories)
        pool_technique_names = {f.name for f in pool}
        pool_tags = {tag for f in pool for tag in f.technique_tags}

        # default: the pool techniques that form the DEFAULT aggregate, from either an
        # explicit set of names or the union over a set of tags. Limited to the pool, so
        # DEFAULT is always a subset of ALL. When it is empty (nothing matched) no DEFAULT
        # aggregate is built and the catalog default falls back to ALL.
        if default_names:
            default_member_names = {f.name for f in pool if f.name in default_names}
        elif default_tags:
            default_member_names = {f.name for f in pool if set(f.technique_tags) & default_tags}
        else:
            default_member_names = set()

        # Auto-promote every catalog tag present in the pool into a selectable aggregate,
        # so tags and aggregates are synonymous: selecting a tag expands to every technique
        # carrying it. "all" and "default" are reserved synthetic aggregates and are never
        # derived from tags. A tag that collides with a technique name stays a concrete
        # technique (name selection wins).
        reserved_aggregate_tags = {"all", "default"}
        auto_aggregate_tags = pool_tags - reserved_aggregate_tags - pool_technique_names
        _validate_generated_member_collisions(
            class_name=class_name,
            factories=pool,
            aggregate_tags=auto_aggregate_tags,
        )

        all_aggregate_tag_names = {"all"} | auto_aggregate_tags
        if default_member_names:
            all_aggregate_tag_names.add("default")

        members: dict[str, tuple[str, set[str]]] = {}

        # Aggregate members first (ALL is always present)
        members["ALL"] = ("all", {"all"})
        if default_member_names:
            members["DEFAULT"] = ("default", {"default"})
        for agg_name in sorted(auto_aggregate_tags):
            members[agg_name.upper()] = (agg_name, {agg_name})

        # Technique members from the pool — tag DEFAULT members so the aggregate expands.
        for factory in pool:
            factory_tags = set(factory.technique_tags)
            if factory.name in default_member_names:
                factory_tags = factory_tags | {"default"}
            members[factory.name] = (factory.name, factory_tags)

        # Build the enum class dynamically
        technique_cls = ScenarioTechnique(class_name, members)

        # Override get_aggregate_tags on the generated class
        @classmethod
        def _get_aggregate_tags(cls: type) -> set[str]:
            return set(all_aggregate_tag_names)

        technique_cls.get_aggregate_tags = _get_aggregate_tags  # type: ignore[ty:invalid-assignment]

        # Record the catalog's default only when a DEFAULT aggregate was actually built.
        # When it wasn't, the attribute is left unset and ScenarioTechnique.default() owns
        # the single ALL fallback — so the "no default -> ALL" rule lives in one place.
        if default_member_names:
            technique_cls._default_technique_value = "default"  # type: ignore[ty:unresolved-attribute]

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
