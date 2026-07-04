# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Core scenario techniques.

Exposes ``get_technique_factories()`` returning the default catalog of
attack technique factories. The ``core`` group tag is injected by
``build_technique_factories`` — factories here carry only their behavioral
tags (e.g. ``single_turn``/``multi_turn``/``default``/``light``).
"""

from pyrit.executor.attack import (
    ContextComplianceAttack,
    ManyShotJailbreakAttack,
    RedTeamingAttack,
    RolePlayAttack,
    RolePlayPaths,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory


def get_technique_factories() -> list[AttackTechniqueFactory]:
    """
    Build the core scenario technique factories.

    Factories that need an adversarial chat target do not bake one in; the
    default adversarial target is resolved lazily inside
    ``AttackTechniqueFactory.create`` via ``get_default_adversarial_target()``.

    A bare ``PromptSendingAttack`` factory is intentionally omitted: every
    scenario whose ``BASELINE_ATTACK_POLICY`` is ``BaselineAttackPolicy.Enabled``
    already auto-prepends an equivalent baseline atomic attack via
    ``Scenario._build_baseline_atomic_attack``.

    Returns:
        list[AttackTechniqueFactory]: The core scenario techniques.
    """
    return [
        AttackTechniqueFactory(
            name="role_play",
            attack_class=RolePlayAttack,
            strategy_tags=["single_turn", "default", "light"],
            attack_kwargs={"role_play_definition_path": RolePlayPaths.MOVIE_SCRIPT.value},
        ),
        AttackTechniqueFactory(
            name="many_shot",
            attack_class=ManyShotJailbreakAttack,
            strategy_tags=["multi_turn", "default", "light"],
        ),
        AttackTechniqueFactory(
            name="tap",
            attack_class=TreeOfAttacksWithPruningAttack,
            strategy_tags=["multi_turn"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_simulated",
            strategy_tags=["single_turn"],
        ),
        AttackTechniqueFactory(
            name="red_teaming",
            attack_class=RedTeamingAttack,
            strategy_tags=["multi_turn", "light"],
        ),
        AttackTechniqueFactory(
            name="context_compliance",
            attack_class=ContextComplianceAttack,
            strategy_tags=["single_turn", "light"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_movie_director",
            strategy_tags=["single_turn"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_history_lecture",
            strategy_tags=["single_turn"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_journalist_interview",
            strategy_tags=["single_turn"],
        ),
    ]
