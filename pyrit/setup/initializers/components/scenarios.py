# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario Technique Initializer for registering persona-driven crescendo techniques.

This module provides the ScenarioTechniqueInitializer class that registers
additional ``AttackTechniqueSpec`` entries into the singleton
``AttackTechniqueRegistry``, on top of the core specs declared in
``pyrit.scenario.core.scenario_techniques.SCENARIO_TECHNIQUES``.

The techniques registered here are persona-driven YAML variants of the canonical
``crescendo_simulated`` technique introduced in PR #1665. They reuse
``PromptSendingAttack`` plus a ``SeedSimulatedConversation`` whose adversarial
chat is driven by a persona-specific YAML system prompt. No new attack
primitives are introduced.

Per-name registration is idempotent: existing entries in the registry are not
overwritten.
"""

import dataclasses
import logging
from pathlib import Path

from pyrit.common.path import EXECUTOR_SEED_PROMPT_PATH
from pyrit.executor.attack import PromptSendingAttack
from pyrit.models import SeedAttackTechniqueGroup, SeedSimulatedConversation
from pyrit.models.seeds.seed_simulated_conversation import NextMessageSystemPromptPaths
from pyrit.registry.object_registries.attack_technique_registry import (
    AttackTechniqueRegistry,
    AttackTechniqueSpec,
)
from pyrit.scenario.core.scenario_techniques import (
    get_default_adversarial_target,
    register_scenario_techniques,
)
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)


# Names of the persona-driven crescendo techniques registered by this initializer.
# Each name corresponds to a YAML file under
# ``pyrit/datasets/executors/red_teaming/<name>.yaml``.
CRESCENDO_MOVIE_DIRECTOR: str = "crescendo_movie_director"
CRESCENDO_HISTORY_LECTURE: str = "crescendo_history_lecture"
CRESCENDO_JOURNALIST_INTERVIEW: str = "crescendo_journalist_interview"

PERSONA_CRESCENDO_TECHNIQUE_NAMES: list[str] = [
    CRESCENDO_MOVIE_DIRECTOR,
    CRESCENDO_HISTORY_LECTURE,
    CRESCENDO_JOURNALIST_INTERVIEW,
]


def _build_persona_crescendo_spec(*, name: str) -> AttackTechniqueSpec:
    """
    Build a persona-driven crescendo ``AttackTechniqueSpec``.

    Mirrors the wiring of the canonical ``crescendo_simulated`` spec from
    ``pyrit.scenario.core.scenario_techniques``: ``PromptSendingAttack`` plus a
    ``SeedSimulatedConversation`` whose adversarial chat reads its system prompt
    from ``pyrit/datasets/executors/red_teaming/<name>.yaml``. ``num_turns``
    matches the canonical default of 3.

    Args:
        name: The technique name. Must match the YAML filename stem under
            ``pyrit/datasets/executors/red_teaming/``.

    Returns:
        AttackTechniqueSpec: A spec ready for adversarial-chat resolution and
        registration via ``AttackTechniqueRegistry.register_from_specs``.
    """
    return AttackTechniqueSpec(
        name=name,
        attack_class=PromptSendingAttack,
        strategy_tags=["core", "single_turn"],
        seed_technique=SeedAttackTechniqueGroup(
            seeds=[
                SeedSimulatedConversation(
                    adversarial_chat_system_prompt_path=(
                        Path(EXECUTOR_SEED_PROMPT_PATH) / "red_teaming" / f"{name}.yaml"
                    ),
                    next_message_system_prompt_path=NextMessageSystemPromptPaths.DIRECT.value,
                    num_turns=3,
                ),
            ],
        ),
    )


def build_persona_crescendo_specs() -> list[AttackTechniqueSpec]:
    """
    Build the full set of persona-driven crescendo specs registered by this initializer.

    Returns:
        list[AttackTechniqueSpec]: One spec per persona variant, in registration order.
    """
    return [_build_persona_crescendo_spec(name=name) for name in PERSONA_CRESCENDO_TECHNIQUE_NAMES]


class ScenarioTechniqueInitializer(PyRITInitializer):
    """
    Register persona-driven crescendo scenario techniques into the registry.

    This initializer first ensures the core ``SCENARIO_TECHNIQUES`` are registered
    (via ``register_scenario_techniques``), then appends the persona-driven
    crescendo variants. Each variant is wired with the same default adversarial
    chat target as ``crescendo_simulated``, since they share the
    ``SeedSimulatedConversation`` shape.

    Registration is per-name idempotent: pre-existing entries in
    ``AttackTechniqueRegistry`` are not overwritten.
    """

    @property
    def name(self) -> str:
        """Get the human-readable name for this initializer."""
        return "Scenario Technique Initializer"

    @property
    def description(self) -> str:
        """Get the description of this initializer."""
        return (
            "Registers persona-driven crescendo scenario techniques (movie director, "
            "history lecture, journalist interview) into the AttackTechniqueRegistry, "
            "on top of the core single_turn_crescendo technique."
        )

    @property
    def execution_order(self) -> int:
        """
        Get the execution order for this initializer.

        Returns 3 to ensure this runs after both ``TargetInitializer`` (order 1)
        and ``ScorerInitializer`` (order 2). The default adversarial chat target,
        if present, is resolved from ``TargetRegistry`` at registration time.
        """
        return 3

    @property
    def required_env_vars(self) -> list[str]:
        """
        Get list of required environment variables.

        Returns an empty list. The default adversarial chat target is resolved
        from ``TargetRegistry`` if available, otherwise falls back to a plain
        ``OpenAIChatTarget`` via ``@apply_defaults``. Either path is acceptable
        here since registration only stores the target reference; the target is
        not invoked at registration time.
        """
        return []

    async def initialize_async(self) -> None:
        """
        Register the persona-driven crescendo specs into the singleton registry.

        First ensures the core ``SCENARIO_TECHNIQUES`` are registered, then
        builds and registers each persona variant with the default adversarial
        chat target baked in. Registration is per-name idempotent.
        """
        register_scenario_techniques()

        default_adversarial = get_default_adversarial_target()
        persona_specs = [
            dataclasses.replace(spec, adversarial_chat=default_adversarial) for spec in build_persona_crescendo_specs()
        ]

        registry = AttackTechniqueRegistry.get_registry_singleton()
        registry.register_from_specs(persona_specs)

        registered_names = [spec.name for spec in persona_specs if spec.name in registry]
        logger.info(
            "Registered %d persona-driven crescendo technique(s): %s",
            len(registered_names),
            ", ".join(registered_names),
        )
