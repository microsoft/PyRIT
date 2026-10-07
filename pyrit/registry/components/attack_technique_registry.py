# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Attack technique registry for PyRIT.

A registry for ``AttackTechniqueFactory`` instances that scenarios and
initializers register and later retrieve. Like ``ConverterRegistry`` it is a
``Registry`` whose pre-configured instances live under the ``instances``
property. It uses ``AttackRegistry`` for attack classes rather than maintaining
another class catalog. ``create_factory`` resolves basic inputs; the factory
constructs the attack only when the scenario supplies execution inputs.

Scenarios and initializers register self-describing factories (via
``register_from_factories``), retrieve them with ``get_factories`` /
``get_factories_or_raise``, filter them in-place by factory properties (e.g.
``factory.uses_adversarial`` or technique tags), and call ``factory.create()``
with the scenario's objective target and scorer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pyrit.models import AttackIdentifier, ComponentType
from pyrit.registry.instance_registry import DefaultInstanceRegistry
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
    """Shared metadata type for the inherited, empty technique class catalog."""


class TechniqueInstanceRegistry(DefaultInstanceRegistry["AttackTechniqueFactory"]):
    """Factory storage with an atomic runtime-admission path."""

    def register_runtime(self, factory: AttackTechniqueFactory) -> None:
        """
        Check selector collisions while holding the same lock as registration.

        Raises:
            ValueError: If the name or a tag conflicts with an existing selector.
        """
        with self._lock:
            entries = self.get_all_instances()
            names = {entry.name.casefold(): entry.name for entry in entries}
            tags = {tag.casefold(): tag for entry in entries for tag in entry.instance.technique_tags}
            folded_name = factory.name.casefold()
            if folded_name in names:
                raise ValueError(f"Technique '{factory.name}' already exists (names are case-insensitive)")
            if folded_name in tags:
                raise ValueError(f"Technique name '{factory.name}' conflicts with an aggregate tag")
            for tag in factory.technique_tags:
                folded = tag.casefold()
                if folded in names or folded == folded_name:
                    raise ValueError(f"Tag '{tag}' conflicts with a technique name")
                if folded in tags and tags[folded] != tag:
                    raise ValueError(f"Tag '{tag}' conflicts with tag '{tags[folded]}'")
            self.register(factory, name=factory.name, tags=factory.technique_tags)


class AttackTechniqueRegistry(Registry["AttackTechniqueFactory", AttackTechniqueMetadata]):
    """
    Registry that holds reusable ``AttackTechniqueFactory`` instances.

    Scenarios and initializers register self-describing
    ``AttackTechniqueFactory`` instances; scenarios retrieve them via
    ``get_factories`` / ``get_factories_or_raise`` and call ``factory.create()``
    with the scenario's objective target and scorer.

    It is a ``Registry``: pre-configured factories live under the ``instances``
    property (``register``, ``get``, ``get_all_instances``, ``get_by_tag``, …),
    a ``DefaultInstanceRegistry``. Attack classes come from ``AttackRegistry``;
    this registry has no separate class catalog.
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
        self.instances = TechniqueInstanceRegistry(instance_type=_attack_technique_factory_type)
        self._scorer_override_policy = ScorerOverridePolicy.WARN

    def create_factory(
        self,
        *,
        name: str,
        attack_type: str,
        params: dict[str, Any] | None = None,
        request_converters: list[str] | None = None,
        response_converters: list[str] | None = None,
        **factory_kwargs: Any,
    ) -> AttackTechniqueFactory:
        """
        Resolve basic registry inputs into a factory without creating an attack.

        Attack types come from ``AttackRegistry``; converters and the adversarial
        target reference existing instances. Seeds, scoring configurations, and
        conversation configurations remain available through Python factories.

        Returns:
            AttackTechniqueFactory: The configured deferred factory.

        Raises:
            ValueError: If the attack or its inputs are not supported.
        """
        from pyrit.executor.attack import AttackConverterConfig
        from pyrit.prompt_normalizer import ConverterConfiguration
        from pyrit.registry.components.attack_registry import AttackRegistry
        from pyrit.registry.resolution import resolve_constructor_args, resolve_reference_value
        from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory

        registry = AttackRegistry.get_registry_singleton()
        try:
            attack_class = registry.get_class(attack_type)
        except KeyError as exc:
            raise ValueError(f"Attack type '{attack_type}' is not registered") from exc
        params = params if params is not None else {}
        deferred = {"objective_target", "attack_adversarial_config", "attack_scoring_config"}
        if params.keys() & deferred:
            raise ValueError(f"These parameters are supplied at execution: {sorted(params.keys() & deferred)}")
        parameters = registry.get_class_metadata(attack_class).parameters
        names = {parameter.name for parameter in parameters}
        supplied = set(params)
        if request_converters is not None or response_converters is not None:
            if "attack_converter_config" in params:
                raise ValueError("Do not combine attack_converter_config with converter name lists")
            if "attack_converter_config" not in names:
                raise ValueError(f"Attack '{attack_type}' does not accept converters")
            supplied.add("attack_converter_config")
        missing = [
            parameter.name
            for parameter in parameters
            if parameter.required and parameter.name not in supplied and parameter.name not in deferred
        ]
        if missing:
            raise ValueError(f"Missing required parameters for '{attack_type}': {missing}")
        attack_args = resolve_constructor_args(
            cls=attack_class,
            raw_args=params,
            identifier_type=AttackIdentifier,
        )
        if request_converters is not None or response_converters is not None:
            converter_args = {}
            for key, values in {
                "request_converters": request_converters,
                "response_converters": response_converters,
            }.items():
                converter_args[key] = ConverterConfiguration.from_converters(
                    converters=[
                        resolve_reference_value(
                            component_type=ComponentType.CONVERTER, value=value, owner="Technique", name=key
                        )
                        for value in values or []
                    ]
                )
            attack_args["attack_converter_config"] = AttackConverterConfig(**converter_args)
        if "attack_adversarial_config" not in names and any(
            factory_kwargs.get(key) is not None
            for key in (
                "adversarial_chat",
                "adversarial_system_prompt",
                "adversarial_seed_prompt",
                "adversarial_prompt_template",
            )
        ):
            raise ValueError(f"Attack '{attack_type}' does not accept adversarial factory settings")
        if factory_kwargs.get("adversarial_chat") is not None:
            factory_kwargs["adversarial_chat"] = resolve_reference_value(
                component_type=ComponentType.TARGET,
                value=factory_kwargs["adversarial_chat"],
                owner="Technique",
                name="adversarial_chat",
            )
        return AttackTechniqueFactory(
            name=name,
            attack_class=attack_class,
            attack_kwargs=attack_args,
            **factory_kwargs,
        )

    @property
    def catalog_revision(self) -> tuple[object, int]:
        """Factory-container identity and mutation revision, including registry resets."""
        return self.instances, self.instances.revision

    def _discover(self) -> None:
        """Register no classes: attack class discovery belongs to ``AttackRegistry``."""

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

        members: dict[str, tuple[str, set[str], str | None]] = {}

        # Aggregate members first (ALL is always present)
        members["ALL"] = ("all", {"all"}, None)
        if default_member_names:
            members["DEFAULT"] = ("default", {"default"}, None)
        for agg_name in sorted(auto_aggregate_tags):
            members[agg_name.upper()] = (agg_name, {agg_name}, None)

        # Technique members from the pool — tag DEFAULT members so the aggregate expands.
        for factory in pool:
            factory_tags = set(factory.technique_tags)
            if factory.name in default_member_names:
                factory_tags = factory_tags | {"default"}
            members[factory.name] = (factory.name, factory_tags, factory.description)

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
