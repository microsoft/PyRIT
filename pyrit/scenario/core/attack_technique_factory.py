# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
AttackTechniqueFactory — Self-describing deferred constructor for AttackTechnique instances.

Captures technique-specific configuration (name, strategy tags, attack class,
attack-class kwargs, optional adversarial chat, optional seed technique) at
construction time. Scenarios produce fresh, fully-constructed attacks by calling
``create()`` with scenario-specific params (objective target, scorer).

The canonical place to register factories is the
``ScenarioTechniqueInitializer`` in
``pyrit.setup.initializers.components.scenario_techniques``. New initializers
register additional factories by calling
``AttackTechniqueRegistry.register_from_factories(...)``.
"""

from __future__ import annotations

import inspect
import logging
import sys
import typing
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from pyrit.common.path import EXECUTOR_SEED_PROMPT_PATH
from pyrit.executor.attack import PromptSendingAttack
from pyrit.executor.attack.core.attack_config import (
    AttackAdversarialConfig,
    AttackScoringConfig,
)
from pyrit.models import (
    ComponentIdentifier,
    Identifiable,
    SeedAttackTechniqueGroup,
    SeedSimulatedConversation,
    build_seed_identifier,
)
from pyrit.models.seeds.seed_simulated_conversation import NextMessageSystemPromptPaths
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.scenario_target_defaults import get_default_adversarial_target

if TYPE_CHECKING:
    from pyrit.executor.attack import AttackStrategy
    from pyrit.executor.attack.core.attack_config import AttackConverterConfig
    from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)


class ScorerOverridePolicy(str, Enum):
    """Policy for what to do when the scenario's scorer is incompatible with an attack's annotation."""

    SKIP = "skip"
    WARN = "warn"
    RAISE = "raise"


class AttackTechniqueFactory(Identifiable):
    """
    A self-describing factory that produces AttackTechnique instances on demand.

    Captures technique-specific configuration (name, strategy tags, converters,
    adversarial config, tree depth, etc.) at construction time. Produces fresh,
    fully-constructed attacks by calling the real constructor with the captured
    params plus scenario-specific objective_target and scoring config.

    Validates kwargs against the attack class constructor signature at
    construction time, catching typos and incompatible parameter names early.
    """

    def __init__(
        self,
        *,
        name: str,
        attack_class: type[AttackStrategy[Any, Any]],
        strategy_tags: list[str] | None = None,
        attack_kwargs: dict[str, Any] | None = None,
        adversarial_config: AttackAdversarialConfig | None = None,
        seed_technique: SeedAttackTechniqueGroup | None = None,
        uses_adversarial: bool | None = None,
        scorer_override_policy: ScorerOverridePolicy = ScorerOverridePolicy.WARN,
    ) -> None:
        """
        Initialize the factory with a technique-specific configuration.

        Args:
            name: Registry name for this technique. This is used as the
                scenario strategy name.
            attack_class: The AttackStrategy subclass to instantiate.
            strategy_tags: Tags controlling which ``ScenarioStrategy``
                aggregates include this technique (e.g. ``"single_turn"``,
                ``"multi_turn"``, ``"default"``).
            attack_kwargs: Keyword arguments to pass to the attack constructor.
                Must not include ``objective_target`` (provided at create time)
                or ``attack_adversarial_config`` (use ``adversarial_config``
                instead).
            adversarial_config: Pre-built adversarial config. Injected into
                the attack at ``create()`` time if the attack class accepts
                ``attack_adversarial_config``. To bake in a bare
                ``PromptTarget``, wrap it as
                ``AttackAdversarialConfig(target=chat)``.
            seed_technique: Optional technique seed group attached to created
                techniques.
            uses_adversarial: Whether this technique drives an adversarial
                chat during execution. ``None`` auto-derives from the attack
                class constructor signature and seed-technique shape.
                Authors can override the derivation explicitly.
            scorer_override_policy: What to do when a scenario's scorer is
                incompatible with the attack's ``attack_scoring_config`` type
                annotation. Defaults to WARN.

        Raises:
            TypeError: If any kwarg name is not a valid constructor parameter,
                or if the attack class constructor uses ``**kwargs``.
            ValueError: If ``objective_target`` or
                ``attack_adversarial_config`` is included in ``attack_kwargs``,
                or if ``uses_adversarial=False`` while an adversarial config
                is wired.
        """
        self._name = name
        self._attack_class = attack_class
        self._strategy_tags = list(strategy_tags) if strategy_tags else []
        self._attack_kwargs = dict(attack_kwargs) if attack_kwargs else {}
        self._adversarial_config = adversarial_config
        self._seed_technique = seed_technique
        self._scorer_override_policy = scorer_override_policy

        self._uses_adversarial = uses_adversarial if uses_adversarial is not None else self._derive_uses_adversarial()

        self._validate_kwargs()
        self._validate_adversarial_flags()

    @classmethod
    def with_simulated_conversation(
        cls,
        *,
        name: str,
        attack_class: type[AttackStrategy[Any, Any]] | None = None,
        adversarial_chat_system_prompt_path: str | Path | None = None,
        next_message_system_prompt_path: str | Path | None = None,
        num_turns: int = 3,
        strategy_tags: list[str] | None = None,
        attack_kwargs: dict[str, Any] | None = None,
        adversarial_config: AttackAdversarialConfig | None = None,
        uses_adversarial: bool | None = None,
        scorer_override_policy: ScorerOverridePolicy = ScorerOverridePolicy.WARN,
    ) -> AttackTechniqueFactory:
        """
        Alternative constructor that builds a ``SeedSimulatedConversation`` inline.

        Wraps a single ``SeedSimulatedConversation`` in a ``SeedAttackTechniqueGroup``
        and assigns it as ``seed_technique`` so callers don't have to construct
        both manually. All other parameters are forwarded to ``__init__``.

        Args:
            name: Registry name for this technique. When other defaults are used,
                ``name`` also picks the canonical YAML at
                ``EXECUTOR_SEED_PROMPT_PATH/red_teaming/{name}.yaml``.
            attack_class: The AttackStrategy subclass to instantiate. Defaults to
                ``PromptSendingAttack``.
            adversarial_chat_system_prompt_path: Path to the YAML file containing
                the adversarial chat system prompt for the simulated conversation.
                Defaults to ``EXECUTOR_SEED_PROMPT_PATH/red_teaming/{name}.yaml``.
            next_message_system_prompt_path: Optional path to the YAML file
                containing the system prompt for generating a final user message
                after the simulated conversation. Defaults to
                ``NextMessageSystemPromptPaths.DIRECT.value``.
            num_turns: Number of simulated conversation turns. Defaults to 3.
            strategy_tags: Tags controlling which ``ScenarioStrategy`` aggregates
                include this technique (e.g. ``"single_turn"``, ``"multi_turn"``,
                ``"default"``). Forwarded to the factory constructor.
            attack_kwargs: Keyword arguments forwarded to the attack constructor.
                Must not include ``objective_target`` (provided at create time)
                or ``attack_adversarial_config`` (use ``adversarial_config``
                instead). Forwarded to the factory constructor.
            adversarial_config: Pre-built adversarial config injected into the
                attack at ``create()`` time if the attack class accepts
                ``attack_adversarial_config``. To bake in a bare
                ``PromptTarget``, wrap it as
                ``AttackAdversarialConfig(target=chat)``. Forwarded to the
                factory constructor.
            uses_adversarial: Whether this technique drives an adversarial chat
                during execution. ``None`` auto-derives from the attack class
                constructor signature and seed-technique shape. Forwarded to
                the factory constructor.
            scorer_override_policy: Policy applied when a scenario's scorer is
                incompatible with the attack's ``attack_scoring_config`` type
                annotation. Defaults to ``WARN``. Forwarded to the factory
                constructor.

        Returns:
            AttackTechniqueFactory: A new factory whose ``seed_technique`` is the
                wrapped simulated conversation.
        """
        if attack_class is None:
            attack_class = PromptSendingAttack
        if adversarial_chat_system_prompt_path is None:
            adversarial_chat_system_prompt_path = Path(EXECUTOR_SEED_PROMPT_PATH) / "red_teaming" / f"{name}.yaml"
        if next_message_system_prompt_path is None:
            next_message_system_prompt_path = NextMessageSystemPromptPaths.DIRECT.value

        seed_technique = SeedAttackTechniqueGroup(
            seeds=[
                SeedSimulatedConversation(
                    adversarial_chat_system_prompt_path=adversarial_chat_system_prompt_path,
                    next_message_system_prompt_path=next_message_system_prompt_path,
                    num_turns=num_turns,
                ),
            ],
        )
        return cls(
            name=name,
            attack_class=attack_class,
            strategy_tags=strategy_tags,
            attack_kwargs=attack_kwargs,
            adversarial_config=adversarial_config,
            seed_technique=seed_technique,
            uses_adversarial=uses_adversarial,
            scorer_override_policy=scorer_override_policy,
        )

    def _derive_uses_adversarial(self) -> bool:
        """
        Auto-derive ``uses_adversarial`` from the attack class signature and seed shape.

        Returns:
            bool: ``True`` if the attack class accepts ``attack_adversarial_config``
                or the seed technique has a simulated conversation.
        """
        sig = inspect.signature(self._attack_class.__init__)
        if "attack_adversarial_config" in sig.parameters:
            return True
        return self._seed_technique is not None and self._seed_technique.has_simulated_conversation

    def _validate_adversarial_flags(self) -> None:
        """
        Validate that ``uses_adversarial`` and ``adversarial_config`` are coherent.

        Raises:
            ValueError: If an adversarial config is wired but ``uses_adversarial=False``.
        """
        if not self._uses_adversarial and self._adversarial_config is not None:
            raise ValueError(
                f"Factory '{self._name}': adversarial_config is set but uses_adversarial=False. "
                f"A technique that doesn't use an adversarial chat should not have one wired."
            )

    def _validate_kwargs(self) -> None:
        """
        Validate that all kwargs are valid parameters for the attack class constructor.

        Uses ``inspect.signature`` on the attack class ``__init__``, which works through
        the ``@apply_defaults`` decorator (it uses ``functools.wraps``).

        Raises:
            TypeError: If any kwarg name is not a valid constructor parameter,
                or if the constructor uses ``**kwargs`` (all parameters must be
                explicitly named).
            ValueError: If ``objective_target`` or ``attack_adversarial_config``
                is included in attack_kwargs.
        """
        if "objective_target" in self._attack_kwargs:
            raise ValueError("objective_target must not be in attack_kwargs — it is provided at create() time.")
        if "attack_adversarial_config" in self._attack_kwargs:
            raise ValueError("attack_adversarial_config must not be in attack_kwargs — use adversarial_config instead.")

        sig = inspect.signature(self._attack_class.__init__)

        # Reject constructors that accept **kwargs — we require explicitly named
        # parameters so that validation is meaningful.
        has_var_keyword = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in sig.parameters.values())
        if has_var_keyword:
            raise TypeError(
                f"{self._attack_class.__name__}.__init__ accepts **kwargs, which prevents "
                f"parameter validation. All attack constructor parameters must be explicitly named."
            )

        valid_params = {
            name
            for name, param in sig.parameters.items()
            if name != "self"
            and param.kind
            in (
                inspect.Parameter.KEYWORD_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        }

        invalid = set(self._attack_kwargs) - valid_params
        if invalid:
            raise TypeError(
                f"Invalid kwargs for {self._attack_class.__name__}: {sorted(invalid)}. "
                f"Valid parameters: {sorted(valid_params)}"
            )

    @property
    def name(self) -> str:
        """The registry name for this technique."""
        return self._name

    @property
    def strategy_tags(self) -> list[str]:
        """Tags controlling which ``ScenarioStrategy`` aggregates include this technique."""
        return list(self._strategy_tags)

    @property
    def tags(self) -> list[str]:
        """Alias for ``strategy_tags`` exposing the Taggable interface (used by ``TagQuery.filter``)."""
        return list(self._strategy_tags)

    @property
    def attack_class(self) -> type[AttackStrategy[Any, Any]]:
        """The attack strategy class this factory produces."""
        return self._attack_class

    @property
    def seed_technique(self) -> SeedAttackTechniqueGroup | None:
        """The optional technique seed group."""
        return self._seed_technique

    @property
    def adversarial_chat(self) -> PromptTarget | None:
        """The adversarial chat target baked into this factory, or None."""
        return self._adversarial_config.target if self._adversarial_config else None

    @property
    def uses_adversarial(self) -> bool:
        """Whether this technique drives an adversarial chat during execution."""
        return self._uses_adversarial

    @property
    def scoring_config_type(self) -> type | None:
        """The required ``attack_scoring_config`` subtype, or ``None`` if any config is accepted."""
        return self._get_scoring_config_type()

    def create(
        self,
        *,
        objective_target: PromptTarget,
        attack_scoring_config: AttackScoringConfig,
        attack_adversarial_config_override: AttackAdversarialConfig | None = None,
        attack_converter_config_override: AttackConverterConfig | None = None,
    ) -> AttackTechnique:
        """
        Create a fresh AttackTechnique bound to the given target.

        Each call produces a fully independent attack instance by calling the
        real constructor. Config objects frozen at factory construction time are
        deep-copied into every new instance.

        The ``*_override`` parameters let a caller **replace** a config that was
        baked into the factory at construction time.  When ``None`` (the
        default), the factory's original config is kept as-is — so baked-in
        converters, adversarial targets, etc. are preserved automatically.

        Override configs are only forwarded when the attack class constructor
        declares a matching parameter (without the ``_override`` suffix).
        This allows a single call site to safely pass all available overrides
        without breaking attacks that don't support them.

        Args:
            objective_target: The target to attack (always required at create time).
            attack_scoring_config: The scoring config to use for the attack. This is important
                for attacks like TAP that may need a more specific scorer than the
                scorer the scenario provides.
            attack_adversarial_config_override: When non-None, replaces any
                adversarial config baked into the factory.  Only forwarded if
                the attack class constructor accepts ``attack_adversarial_config``.
            attack_converter_config_override: When non-None, replaces any
                converter config baked into the factory.  Only forwarded if
                the attack class constructor accepts ``attack_converter_config``.

        Returns:
            A fresh AttackTechnique with a newly-constructed attack strategy.

        Raises:
            ValueError: If ``attack_adversarial_config_override`` is supplied but
                the factory already has an adversarial config baked in at
                construction time, or if ``scorer_override_policy`` is RAISE and
                the override config is incompatible with the attack's type annotation.
        """
        if attack_adversarial_config_override is not None and self._adversarial_config is not None:
            raise ValueError(
                f"Factory '{self._name}': adversarial config was baked in at construction; "
                f"cannot supply attack_adversarial_config_override."
            )

        adversarial_config = self._adversarial_config
        if self._uses_adversarial and adversarial_config is None and attack_adversarial_config_override is None:
            adversarial_config = self._resolve_default_adversarial_config()

        kwargs = dict(self._attack_kwargs)
        kwargs["objective_target"] = objective_target

        accepted_params = self._get_accepted_params()
        if self._should_apply_scoring_config(
            attack_scoring_config=attack_scoring_config,
            accepted_params=accepted_params,
        ):
            kwargs["attack_scoring_config"] = attack_scoring_config
        if "attack_adversarial_config" in accepted_params:
            if attack_adversarial_config_override is not None:
                kwargs["attack_adversarial_config"] = attack_adversarial_config_override
            elif adversarial_config is not None:
                kwargs["attack_adversarial_config"] = adversarial_config
        if attack_converter_config_override is not None and "attack_converter_config" in accepted_params:
            kwargs["attack_converter_config"] = attack_converter_config_override

        attack = self._attack_class(**kwargs)
        return AttackTechnique(attack=attack, seed_technique=self._seed_technique)

    @staticmethod
    def _resolve_default_adversarial_config() -> AttackAdversarialConfig:
        """
        Lazily resolve the default adversarial chat target and wrap it in a config.

        Returns:
            AttackAdversarialConfig: Config wrapping the default adversarial chat target.
        """
        return AttackAdversarialConfig(target=get_default_adversarial_target())

    def _get_accepted_params(self) -> set[str]:
        """Return the set of keyword parameter names accepted by the attack class constructor."""
        sig = inspect.signature(self._attack_class.__init__)
        return {
            name
            for name, param in sig.parameters.items()
            if name != "self"
            and param.kind
            in (
                inspect.Parameter.KEYWORD_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        }

    def _should_apply_scoring_config(
        self,
        *,
        attack_scoring_config: AttackScoringConfig,
        accepted_params: set[str],
    ) -> bool:
        """
        Determine whether the scoring config should be forwarded to the attack constructor.

        Checks two conditions:
        1. The attack class accepts an ``attack_scoring_config`` parameter.
        2. The provided config is type-compatible with the attack's annotation.

        When either condition fails, the ``scorer_override_policy`` determines
        behavior: RAISE raises ValueError, WARN logs and returns False, SKIP
        silently returns False.

        Args:
            attack_scoring_config: The scoring config to evaluate.
            accepted_params: The set of parameter names the attack class accepts.

        Returns:
            True if the config should be applied, False otherwise.

        Raises:
            ValueError: If the policy is RAISE and the config cannot be applied.
        """
        if "attack_scoring_config" not in accepted_params:
            self._apply_scorer_policy(
                f"Scorer config provided but {self._attack_class.__name__} does not accept 'attack_scoring_config'."
            )
            return False

        required_type = self._get_scoring_config_type()
        if required_type is None or isinstance(attack_scoring_config, required_type):
            return True

        self._apply_scorer_policy(
            f"Scorer config of type {type(attack_scoring_config).__name__} is incompatible "
            f"with {self._attack_class.__name__} (requires {required_type.__name__})."
        )
        return False

    def _apply_scorer_policy(self, message: str) -> None:
        """
        Apply the scorer override policy for an incompatibility.

        Args:
            message: Description of the incompatibility.

        Raises:
            ValueError: If the policy is RAISE.
        """
        if self._scorer_override_policy == ScorerOverridePolicy.RAISE:
            raise ValueError(message)
        if self._scorer_override_policy == ScorerOverridePolicy.WARN:
            logger.warning(message)

    def _get_scoring_config_type(self) -> type | None:
        """
        Introspect the attack class to determine the required type for ``attack_scoring_config``.

        Resolves the type annotation (handling ``Optional[X]`` / ``X | None``) and returns
        the inner concrete type. Returns ``None`` if the annotation is the base
        ``AttackScoringConfig`` or cannot be resolved — meaning any config is accepted.

        Returns:
            The narrowed type if the annotation is narrower than the base, else None.
        """
        try:
            # get_type_hints resolves string annotations from __future__ annotations
            hints = typing.get_type_hints(
                self._attack_class.__init__,
                globalns=getattr(sys.modules.get(self._attack_class.__module__, None), "__dict__", None),
            )
        except Exception:
            return None

        annotation = hints.get("attack_scoring_config")
        if annotation is None:
            return None

        inner = self._unwrap_optional(annotation)
        if inner is None or inner is AttackScoringConfig:
            # Base type or unresolvable — any config is accepted
            return None
        return inner

    @staticmethod
    def _unwrap_optional(annotation: Any) -> type | None:
        """
        Unwrap ``Optional[X]``, ``X | None``, or ``Union[X, None]`` to extract X.

        Returns:
            The inner type X, or None if the annotation cannot be unwrapped to a single type.
        """
        # Handle Python 3.10+ union syntax (types.UnionType): X | None
        origin = typing.get_origin(annotation)
        if origin is Union or (hasattr(annotation, "__args__") and origin is None and hasattr(annotation, "__or__")):
            args = typing.get_args(annotation)
            non_none = [a for a in args if a is not type(None)]
            return non_none[0] if len(non_none) == 1 else None

        # types.UnionType from PEP 604 at runtime (3.10+)
        if hasattr(annotation, "__args__") and type(annotation).__name__ == "UnionType":
            args = annotation.__args__
            non_none = [a for a in args if a is not type(None)]
            return non_none[0] if len(non_none) == 1 else None

        # Plain type (not Optional)
        if isinstance(annotation, type):
            return annotation

        return None

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        """
        Convert a value to a JSON-safe representation for identifier hashing.

        Primitives are included directly. Identifiable objects contribute their
        hash. Collections are serialized recursively. Other types fall back to
        their qualified class name.

        Returns:
            Any: A JSON-serializable representation of the value.
        """
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [AttackTechniqueFactory._serialize_value(v) for v in value]
        if isinstance(value, dict):
            return {str(k): AttackTechniqueFactory._serialize_value(v) for k, v in sorted(value.items())}
        if isinstance(value, Identifiable):
            return value.get_identifier().hash
        return f"<{type(value).__qualname__}>"

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the behavioral identity for this factory.

        Includes the factory name, attack class, kwargs, adversarial config,
        and the adversarial-flag booleans so factories with different
        configurations produce different hashes. When a seed technique is
        present, its seeds are added as ``children["technique_seeds"]``.

        Returns:
            ComponentIdentifier: The frozen identity snapshot.
        """
        kwargs_for_id = {k: self._serialize_value(v) for k, v in sorted(self._attack_kwargs.items())}
        params: dict[str, Any] = {
            "name": self._name,
            "attack_class": self._attack_class.__name__,
            "kwargs": kwargs_for_id,
            "uses_adversarial": self._uses_adversarial,
        }
        if self._strategy_tags:
            params["strategy_tags"] = list(self._strategy_tags)
        if self._adversarial_config is not None:
            params["adversarial_config"] = self._serialize_value(self._adversarial_config)

        children: dict[str, Any] = {}
        if self._seed_technique is not None:
            technique_seed_ids = [build_seed_identifier(seed) for seed in self._seed_technique.seeds]
            if technique_seed_ids:
                children["technique_seeds"] = technique_seed_ids

        return ComponentIdentifier.of(self, params=params, children=children)
