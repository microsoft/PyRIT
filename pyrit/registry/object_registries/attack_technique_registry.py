# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
AttackTechniqueRegistry — Singleton registry of reusable attack technique factories.

Scenarios and initializers register technique factories (capturing technique-specific
config). Scenarios retrieve them via ``create_technique()``, which calls the factory
with the scenario's objective target and scorer.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pyrit.registry.object_registries.base_instance_registry import (
    BaseInstanceRegistry,
)

if TYPE_CHECKING:
    from pyrit.executor.attack.core.attack_config import (
        AttackAdversarialConfig,
        AttackConverterConfig,
        AttackScoringConfig,
    )
    from pyrit.prompt_target import PromptChatTarget, PromptTarget
    from pyrit.registry.tag_query import TagQuery
    from pyrit.scenario.core.attack_technique import AttackTechnique
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AttackTechniqueSpec:
    """
    Declarative definition of an attack technique.

    The registry converts specs into ``AttackTechniqueFactory`` instances.
    A minimal spec only needs ``name`` and ``attack_class``::

        AttackTechniqueSpec(name="prompt_sending", attack_class=PromptSendingAttack)

    Use ``extra_kwargs`` for constructor arguments specific to a particular
    attack class (as opposed to common arguments like ``objective_target``
    and ``attack_scoring_config``, which the factory injects automatically)::

        AttackTechniqueSpec(
            name="role_play",
            attack_class=RolePlayAttack,
            strategy_tags=["core", "single_turn"],
            extra_kwargs={"role_play_definition_path": RolePlayPaths.MOVIE_SCRIPT.value},
        )

    Attacks that need an adversarial chat target should set
    ``adversarial_chat`` (resolved target) or ``adversarial_chat_key``
    (deferred ``TargetRegistry`` key resolved at runtime by
    ``build_scenario_techniques()``). These are mutually exclusive.
    The registry automatically injects an ``AttackAdversarialConfig`` when
    the attack class accepts one and ``adversarial_chat`` is set.

    Args:
        name: Registry name (must match the strategy enum value).
        attack_class: The ``AttackStrategy`` subclass (e.g.
            ``PromptSendingAttack``, ``TreeOfAttacksWithPruningAttack``).
        strategy_tags: Tags controlling which ``ScenarioStrategy`` aggregates
            include this technique (e.g. ``"single_turn"``, ``"multi_turn"``).
        adversarial_chat: Live adversarial chat target for multi-turn attacks.
            Part of technique identity. Mutually exclusive with
            ``adversarial_chat_key``.
        adversarial_chat_key: Deferred ``TargetRegistry`` key resolved into
            ``adversarial_chat`` at runtime. Use in static spec catalogs
            where the target isn't available yet.
        extra_kwargs: Attack-class-specific keyword arguments forwarded to
            the constructor, e.g. ``{"tree_width": 5}`` for
            ``TreeOfAttacksWithPruningAttack``. Must not contain
            ``attack_adversarial_config`` (use ``adversarial_chat``) or
            factory-injected args (``objective_target``,
            ``attack_scoring_config``).
        accepts_scorer_override: Whether the technique accepts a scenario-level
            scorer override. Set to ``False`` for techniques (e.g. TAP) that
            manage their own scoring. Defaults to ``True``.
    """

    name: str
    attack_class: type
    strategy_tags: list[str] = field(default_factory=list)
    adversarial_chat: PromptChatTarget | None = field(default=None)
    adversarial_chat_key: str | None = None
    extra_kwargs: dict[str, Any] = field(default_factory=dict)
    accepts_scorer_override: bool = True

    @property
    def tags(self) -> list[str]:
        """Return strategy_tags as the Taggable interface."""
        return self.strategy_tags

    def __post_init__(self) -> None:
        """
        Validate mutually exclusive fields.

        Raises:
            ValueError: If both adversarial_chat and adversarial_chat_key are set.
        """
        if self.adversarial_chat and self.adversarial_chat_key:
            raise ValueError(
                f"Technique spec '{self.name}' sets both adversarial_chat and "
                f"adversarial_chat_key — these are mutually exclusive."
            )


class AttackTechniqueRegistry(BaseInstanceRegistry["AttackTechniqueFactory"]):
    """
    Singleton registry of reusable attack technique factories.

    Scenarios and initializers register technique factories (capturing
    technique-specific config). Scenarios retrieve them via ``create_technique()``,
    which calls the factory with the scenario's objective target and scorer.
    """

    def register_technique(
        self,
        *,
        name: str,
        factory: AttackTechniqueFactory,
        tags: dict[str, str] | list[str] | None = None,
        accepts_scorer_override: bool = True,
    ) -> None:
        """
        Register an attack technique factory.

        Args:
            name: The registry name for this technique.
            factory: The factory that produces attack techniques.
            tags: Optional tags for categorisation. Accepts a ``dict[str, str]``
                or a ``list[str]`` (each string becomes a key with value ``""``).
            accepts_scorer_override: Whether the technique accepts a scenario-level
                scorer override. Defaults to True.
        """
        self.register(
            factory,
            name=name,
            tags=tags,
            metadata={"accepts_scorer_override": accepts_scorer_override},
        )
        logger.debug(f"Registered attack technique factory: {name} ({factory.attack_class.__name__})")

    def get_factories(self) -> dict[str, AttackTechniqueFactory]:
        """
        Return all registered factories as a name→factory dict.

        Returns:
            dict[str, AttackTechniqueFactory]: Mapping of technique name to factory.
        """
        return {name: entry.instance for name, entry in self._registry_items.items()}

    def accepts_scorer_override(self, name: str) -> bool:
        """
        Check whether a registered technique accepts a scenario-level scorer override.

        Returns True by default if the tag is not set (for backwards compatibility
        with externally registered techniques).

        Args:
            name: The registry name of the technique.

        Returns:
            bool: True if the technique accepts scorer overrides.

        Raises:
            KeyError: If no technique is registered with the given name.
        """
        entry = self._registry_items[name]
        return bool(entry.metadata.get("accepts_scorer_override", True))

    def create_technique(
        self,
        name: str,
        *,
        objective_target: PromptTarget,
        attack_scoring_config_override: AttackScoringConfig | None = None,
        attack_adversarial_config_override: AttackAdversarialConfig | None = None,
        attack_converter_config_override: AttackConverterConfig | None = None,
    ) -> AttackTechnique:
        """
        Retrieve a factory by name and produce a fresh attack technique.

        Args:
            name: The registry name of the technique.
            objective_target: The target to attack.
            attack_scoring_config_override: When non-None, replaces any scoring
                config baked into the factory.
            attack_adversarial_config_override: When non-None, replaces any
                adversarial config baked into the factory.
            attack_converter_config_override: When non-None, replaces any
                converter config baked into the factory.

        Returns:
            A fresh AttackTechnique with a newly-constructed attack strategy.

        Raises:
            KeyError: If no technique is registered with the given name.
        """
        entry = self._registry_items.get(name)
        if entry is None:
            raise KeyError(f"No technique registered with name '{name}'")
        return entry.instance.create(
            objective_target=objective_target,
            attack_scoring_config_override=attack_scoring_config_override,
            attack_adversarial_config_override=attack_adversarial_config_override,
            attack_converter_config_override=attack_converter_config_override,
        )

    @staticmethod
    def build_strategy_class_from_specs(
        *,
        class_name: str,
        specs: list[AttackTechniqueSpec],
        aggregate_tags: dict[str, TagQuery],
    ) -> type:
        """
        Build a ``ScenarioStrategy`` enum subclass dynamically from technique specs.

        Creates an enum class with:
        - An ``ALL`` aggregate member (always included).
        - Additional aggregate members from ``aggregate_tags`` keys.
        - One technique member per spec, with tags from the spec.

        Each aggregate maps to a :class:`TagQuery` that determines which
        technique specs belong to it.

        This reads from the **spec list** (pure data), not from the mutable
        registry. This ensures deterministic output regardless of registry state.

        Args:
            class_name: Name for the generated enum class.
            specs: Technique specifications to include as enum members.
            aggregate_tags: Maps aggregate member names to a :class:`TagQuery`
                that selects which techniques belong to the aggregate.
                An ``ALL`` aggregate (expanding to all techniques) is always added.

        Returns:
            A ``ScenarioStrategy`` subclass with the generated members.
        """
        from pyrit.scenario.core.scenario_strategy import ScenarioStrategy

        all_aggregate_tag_names = {"all"} | set(aggregate_tags.keys())

        members: dict[str, tuple[str, set[str]]] = {}

        # Aggregate members first (ALL is always present)
        members["ALL"] = ("all", {"all"})
        for agg_name in aggregate_tags:
            members[agg_name.upper()] = (agg_name, {agg_name})

        # Technique members from specs — assign aggregate tags based on TagQuery matching
        for spec in specs:
            spec_tags = set(spec.strategy_tags)
            matched_agg_tags = {agg_name for agg_name, query in aggregate_tags.items() if query.matches(spec_tags)}
            members[spec.name] = (spec.name, spec_tags | matched_agg_tags)

        # Build the enum class dynamically
        strategy_cls = ScenarioStrategy(class_name, members)  # type: ignore[arg-type]

        # Override get_aggregate_tags on the generated class
        @classmethod  # type: ignore[misc]
        def _get_aggregate_tags(cls: type) -> set[str]:
            return set(all_aggregate_tag_names)

        strategy_cls.get_aggregate_tags = _get_aggregate_tags  # type: ignore[method-assign, assignment]

        return strategy_cls  # type: ignore[return-value]

    @staticmethod
    def build_factory_from_spec(spec: AttackTechniqueSpec) -> AttackTechniqueFactory:
        """
        Build an ``AttackTechniqueFactory`` from an ``AttackTechniqueSpec``.

        Injects ``AttackAdversarialConfig`` when both ``spec.adversarial_chat``
        is set and the attack class accepts ``attack_adversarial_config`` as a
        constructor parameter.  If ``adversarial_chat`` is set but the class
        does not accept it, a warning is logged and the field is ignored.

        Args:
            spec: The technique specification. Must not contain
                ``attack_adversarial_config`` in ``extra_kwargs``; use
                ``spec.adversarial_chat`` instead.

        Returns:
            AttackTechniqueFactory: A factory ready for registration.

        Raises:
            ValueError: If ``extra_kwargs`` contains the reserved key
                ``attack_adversarial_config``.
        """
        from pyrit.executor.attack import AttackAdversarialConfig
        from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory

        if "attack_adversarial_config" in spec.extra_kwargs:
            raise ValueError(
                f"Spec '{spec.name}': 'attack_adversarial_config' must not appear in extra_kwargs. "
                "Set spec.adversarial_chat instead."
            )

        kwargs: dict[str, Any] = dict(spec.extra_kwargs)

        if spec.adversarial_chat is not None:
            if AttackTechniqueRegistry._accepts_adversarial(spec.attack_class):
                kwargs["attack_adversarial_config"] = AttackAdversarialConfig(target=spec.adversarial_chat)
            else:
                logger.warning(
                    "Spec '%s': adversarial_chat is set but %s does not accept "
                    "'attack_adversarial_config'. The adversarial_chat will be ignored.",
                    spec.name,
                    spec.attack_class.__name__,
                )

        return AttackTechniqueFactory(
            attack_class=spec.attack_class,
            attack_kwargs=kwargs or None,
        )

    @staticmethod
    def _accepts_adversarial(attack_class: type) -> bool:
        """
        Check if an attack class accepts ``attack_adversarial_config``.

        Returns:
            bool: Whether the parameter is present in the class constructor.
        """
        sig = inspect.signature(attack_class.__init__)  # type: ignore[misc]
        return "attack_adversarial_config" in sig.parameters

    def register_from_specs(
        self,
        specs: list[AttackTechniqueSpec],
    ) -> None:
        """
        Build factories from specs and register them.

        Per-name idempotent: existing entries are not overwritten.

        Args:
            specs: Technique specifications to register. Each spec is
                self-contained: the adversarial chat target (if any) is
                declared on the spec itself via ``spec.adversarial_chat``.
        """
        for spec in specs:
            if spec.name not in self:
                factory = self.build_factory_from_spec(spec)
                tags: dict[str, str] = dict.fromkeys(spec.strategy_tags, "")
                self.register_technique(
                    name=spec.name,
                    factory=factory,
                    tags=tags,
                    accepts_scorer_override=spec.accepts_scorer_override,
                )

        logger.debug("Technique registration complete (%d total in registry)", len(self))
