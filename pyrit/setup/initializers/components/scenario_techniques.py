# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario technique initializer.

This module owns the canonical catalog of scenario attack techniques as a
flat list of self-describing :class:`AttackTechniqueFactory` instances and
registers them into the singleton :class:`AttackTechniqueRegistry` via
:class:`ScenarioTechniqueInitializer`.

Per-name registration is idempotent: pre-existing entries in the registry are
not overwritten.
"""

from __future__ import annotations

import logging

from pyrit.executor.attack import (
    ContextComplianceAttack,
    ManyShotJailbreakAttack,
    PAIRAttack,
    RedTeamingAttack,
    RolePlayAttack,
    RolePlayPaths,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.registry.object_registries.attack_technique_registry import (
    AttackTechniqueRegistry,
)
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)


def build_scenario_technique_factories() -> list[AttackTechniqueFactory]:
    """
    Build the canonical scenario technique factories.

    Factories that need an adversarial chat target do not bake one in; the
    default adversarial target is resolved lazily inside
    :meth:`AttackTechniqueFactory.create` via
    ``get_default_adversarial_target()``. Scenarios may also pass
    ``attack_adversarial_config_override`` at create time (but only when the
    factory did not bake one in at construction).

    A bare ``PromptSendingAttack`` factory is intentionally omitted from the
    catalog: every scenario whose ``BASELINE_ATTACK_POLICY`` is
    :attr:`BaselineAttackPolicy.Enabled` already auto-prepends an equivalent
    baseline atomic attack via ``Scenario._build_baseline_atomic_attack``.

    Returns:
        list[AttackTechniqueFactory]: The full catalog of scenario techniques.
    """
    return [
        AttackTechniqueFactory(
            name="role_play",
            attack_class=RolePlayAttack,
            strategy_tags=["core", "single_turn", "default", "light"],
            attack_kwargs={"role_play_definition_path": RolePlayPaths.MOVIE_SCRIPT.value},
        ),
        AttackTechniqueFactory(
            name="many_shot",
            attack_class=ManyShotJailbreakAttack,
            strategy_tags=["core", "multi_turn", "default", "light"],
        ),
        AttackTechniqueFactory(
            name="tap",
            attack_class=TreeOfAttacksWithPruningAttack,
            strategy_tags=["core", "multi_turn"],
        ),
        AttackTechniqueFactory(
            name="pair",
            attack_class=PAIRAttack,
            strategy_tags=["core", "multi_turn"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_simulated",
            strategy_tags=["core", "single_turn"],
        ),
        AttackTechniqueFactory(
            name="red_teaming",
            attack_class=RedTeamingAttack,
            strategy_tags=["core", "multi_turn", "light"],
        ),
        AttackTechniqueFactory(
            name="context_compliance",
            attack_class=ContextComplianceAttack,
            strategy_tags=["core", "single_turn", "light"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_movie_director",
            strategy_tags=["core", "single_turn"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_history_lecture",
            strategy_tags=["core", "single_turn"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_journalist_interview",
            strategy_tags=["core", "single_turn"],
        ),
    ]


class ScenarioTechniqueInitializer(PyRITInitializer):
    """
    Register the canonical scenario attack technique factories.

    Builds and registers the 6 core techniques (``role_play``, ``many_shot``,
    ``tap``, ``crescendo_simulated``, ``red_teaming``, ``context_compliance``)
    together with the persona-driven crescendo variants
    (``crescendo_movie_director``, ``crescendo_history_lecture``,
    ``crescendo_journalist_interview``).

    A bare ``PromptSendingAttack`` factory is intentionally not registered: the
    scenario-level baseline (``BaselineAttackPolicy.Enabled`` +
    ``Scenario._build_baseline_atomic_attack``) already covers that case.

    Registration is per-name idempotent: pre-existing entries in
    :class:`AttackTechniqueRegistry` are not overwritten.
    """

    async def initialize_async(self) -> None:
        """Build the canonical factories and register them into the singleton registry."""
        factories = build_scenario_technique_factories()

        registry = AttackTechniqueRegistry.get_registry_singleton()
        registry.register_from_factories(factories)

        registered_names = [f.name for f in factories if f.name in registry]
        logger.info(
            "Registered %d scenario technique factory(ies): %s",
            len(registered_names),
            ", ".join(registered_names),
        )
