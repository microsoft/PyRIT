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
from typing import TYPE_CHECKING, Any, Callable

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
    from pyrit.scenario.core.attack_technique import AttackTechnique
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TechniqueSpec:
    """
    Declarative definition of an attack technique.

    Each spec describes one registrable technique. The registry converts
    specs into ``AttackTechniqueFactory`` instances and registers them.

    Whether a technique receives an ``AttackAdversarialConfig`` is determined
    automatically: the registry inspects the attack class constructor and
    injects one when ``attack_adversarial_config`` is an accepted parameter.

    Args:
        name: Registry name (must match the strategy enum value).
        attack_class: The ``AttackStrategy`` subclass.
        tags: Classification tags (e.g. ``["single_turn"]``).
        extra_kwargs_builder: Optional callback that returns additional kwargs
            for the factory. Receives the resolved adversarial target.
        accepts_scorer_override: Whether the technique accepts a scenario-level
            scorer override. Set to False for techniques (e.g. TAP) that manage
            their own scoring internally. Defaults to True.
    """

    name: str
    attack_class: type
    tags: list[str] = field(default_factory=list)
    extra_kwargs_builder: Callable[["PromptChatTarget"], dict[str, Any]] | None = None
    accepts_scorer_override: bool = True


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
    ) -> None:
        """
        Register an attack technique factory.

        Args:
            name: The registry name for this technique.
            factory: The factory that produces attack techniques.
            tags: Optional tags for categorisation. Accepts a ``dict[str, str]``
                or a ``list[str]`` (each string becomes a key with value ``""``).
        """
        self.register(factory, name=name, tags=tags)
        logger.debug(f"Registered attack technique factory: {name} ({factory.attack_class.__name__})")

    def get_factories(self) -> dict[str, "AttackTechniqueFactory"]:
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
        return entry.tags.get("accepts_scorer_override", "true") == "true"

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
        specs: list[TechniqueSpec],
        aggregate_tags: dict[str, set[str]],
    ) -> type:
        """
        Build a ``ScenarioStrategy`` enum subclass dynamically from technique specs.

        Creates an enum class with:
        - An ``ALL`` aggregate member (always included).
        - Additional aggregate members from ``aggregate_tags`` keys.
        - One technique member per spec, with tags from the spec.

        This reads from the **spec list** (pure data), not from the mutable
        registry. This ensures deterministic output regardless of registry state.

        Args:
            class_name: Name for the generated enum class.
            specs: Technique specifications to include as enum members.
            aggregate_tags: Maps aggregate member names to the set of tags they
                expand to. For example, ``{"default": {"default"}, "single_turn": {"single_turn"}}``.
                An ``ALL`` aggregate (expanding to all techniques) is always added.

        Returns:
            A ``ScenarioStrategy`` subclass with the generated members.
        """
        from pyrit.scenario.core.scenario_strategy import ScenarioStrategy

        all_aggregate_tag_names = {"all"} | set(aggregate_tags.keys())

        members: dict[str, tuple[str, set[str]]] = {}

        # Aggregate members first (ALL is always present)
        members["ALL"] = ("all", {"all"})
        for agg_name, agg_tag_set in aggregate_tags.items():
            members[agg_name.upper()] = (agg_name, {agg_name})

        # Technique members from specs
        for spec in specs:
            tag_set = {t for t in spec.tags if t not in ("accepts_scorer_override",)}
            members[spec.name] = (spec.name, tag_set)

        # Build the enum class dynamically
        strategy_cls = ScenarioStrategy(class_name, members)

        # Override get_aggregate_tags on the generated class
        @classmethod  # type: ignore[misc]
        def _get_aggregate_tags(cls: type) -> set[str]:
            return set(all_aggregate_tag_names)

        strategy_cls.get_aggregate_tags = _get_aggregate_tags  # type: ignore[attr-defined]

        return strategy_cls

    @staticmethod
    def build_factory_from_spec(
        spec: TechniqueSpec,
        *,
        adversarial_chat: "PromptChatTarget | None" = None,
    ) -> "AttackTechniqueFactory":
        """
        Build an ``AttackTechniqueFactory`` from a ``TechniqueSpec``.

        Automatically injects ``AttackAdversarialConfig`` when the attack
        class accepts ``attack_adversarial_config`` as a constructor parameter.

        Args:
            spec: The technique specification.
            adversarial_chat: Shared adversarial chat target for techniques
                that require one. If None, no adversarial config is injected.

        Returns:
            AttackTechniqueFactory: A factory ready for registration.
        """
        from pyrit.executor.attack import AttackAdversarialConfig
        from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory

        kwargs: dict[str, Any] = {}

        if adversarial_chat is not None and AttackTechniqueRegistry._accepts_adversarial(spec.attack_class):
            kwargs["attack_adversarial_config"] = AttackAdversarialConfig(target=adversarial_chat)

        if spec.extra_kwargs_builder:
            kwargs.update(spec.extra_kwargs_builder(adversarial_chat))

        return AttackTechniqueFactory(
            attack_class=spec.attack_class,
            attack_kwargs=kwargs or None,
        )

    @staticmethod
    def _accepts_adversarial(attack_class: type) -> bool:
        """Check if an attack class accepts ``attack_adversarial_config``."""
        sig = inspect.signature(attack_class.__init__)
        return "attack_adversarial_config" in sig.parameters

    def register_from_specs(
        self,
        specs: list[TechniqueSpec],
        *,
        adversarial_chat: "PromptChatTarget | None" = None,
    ) -> None:
        """
        Build factories from specs and register them.

        Per-name idempotent: existing entries are not overwritten.

        Args:
            specs: Technique specifications to register.
            adversarial_chat: Shared adversarial chat target for techniques
                that require one.
        """
        for spec in specs:
            if spec.name not in self:
                factory = self.build_factory_from_spec(spec, adversarial_chat=adversarial_chat)
                tags: dict[str, str] = {t: "" for t in spec.tags}
                tags["accepts_scorer_override"] = str(spec.accepts_scorer_override).lower()
                self.register_technique(name=spec.name, factory=factory, tags=tags)

        logger.debug("Technique registration complete (%d total in registry)", len(self))
