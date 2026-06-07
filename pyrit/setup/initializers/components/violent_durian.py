# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Violent Durian attack technique initializer.

Registers the optional ``violent_durian`` attack technique — a ``RedTeamingAttack``
configured with a criminal-persona adversarial system prompt and the distinctive
"durian" seed prompt — into the singleton ``AttackTechniqueRegistry``.

This technique is intentionally **not** part of the default scenario technique catalog
(``build_scenario_technique_factories``). It is opt-in: run this initializer explicitly
(e.g. ``--initializers violent_durian``) to make the technique available to scenarios.

Adapted from Project Moonshot's Violent Durian attack module:
https://github.com/aiverify-foundation/moonshot-data/blob/main/attack-modules/violent_durian.py
"""

from __future__ import annotations

import logging

from pyrit.common.path import EXECUTOR_RED_TEAM_PATH
from pyrit.executor.attack import RedTeamingAttack
from pyrit.models import SeedPrompt
from pyrit.registry.object_registries.attack_technique_registry import (
    AttackTechniqueRegistry,
)
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)

VIOLENT_DURIAN_SYSTEM_PROMPT_PATH = EXECUTOR_RED_TEAM_PATH / "violent_durian.yaml"
VIOLENT_DURIAN_SEED_PROMPT_PATH = EXECUTOR_RED_TEAM_PATH / "violent_durian_seed_prompt.yaml"


def build_violent_durian_factory() -> AttackTechniqueFactory:
    """
    Build the ``violent_durian`` attack technique factory.

    The factory wires a criminal-persona adversarial system prompt and the durian seed
    prompt while keeping the adversarial target lazy (resolved at ``create()`` time).

    Returns:
        AttackTechniqueFactory: The opt-in Violent Durian technique factory.
    """
    return AttackTechniqueFactory(
        name="violent_durian",
        attack_class=RedTeamingAttack,
        strategy_tags=["multi_turn"],
        adversarial_system_prompt_path=VIOLENT_DURIAN_SYSTEM_PROMPT_PATH,
        adversarial_seed_prompt=SeedPrompt.from_yaml_file(VIOLENT_DURIAN_SEED_PROMPT_PATH),
    )


class ViolentDurianInitializer(PyRITInitializer):
    """
    Register the optional ``violent_durian`` attack technique.

    Violent Durian is a multi-turn ``RedTeamingAttack`` that manipulates the target into
    adopting a violent criminal persona and providing illegal or dangerous content. It is
    opt-in and excluded from the default scenario technique catalog, so it is never run by
    default; run this initializer to make it available as a scenario technique option.

    Registration is per-name idempotent: a pre-existing ``violent_durian`` entry in
    ``AttackTechniqueRegistry`` is not overwritten.
    """

    async def initialize_async(self) -> None:
        """Build the Violent Durian factory and register it into the singleton registry."""
        factory = build_violent_durian_factory()

        registry = AttackTechniqueRegistry.get_registry_singleton()
        registry.register_from_factories([factory])

        logger.info("Registered Violent Durian attack technique factory: %s", factory.name)
