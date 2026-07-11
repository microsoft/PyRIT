# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Core scenario techniques.

``core`` is the home for general-purpose attack techniques usable by any
scenario. The ``core`` group tag is injected by ``build_technique_factories`` —
factories here carry only their behavioral tags (e.g.
``single_turn``/``multi_turn``/``light``).

``default`` is intentionally not a tag here: what runs by default is
scenario-relative and is declared per scenario (see
``AttackTechniqueRegistry.build_technique_class_from_factories``'s
``default_technique_names``), not baked into the shared catalog.
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

    A bare ``PromptSendingAttack`` factory is intentionally omitted: every
    scenario whose ``BASELINE_ATTACK_POLICY`` is ``BaselineAttackPolicy.Enabled``
    already auto-prepends an equivalent baseline atomic attack via
    ``Scenario._build_baseline_atomic_attack``.

    Factories that need an adversarial chat target do not bake one in; the
    default adversarial target is resolved lazily inside
    ``AttackTechniqueFactory.create`` via ``get_default_adversarial_target()``.

    Returns:
        list[AttackTechniqueFactory]: The core scenario techniques.
    """
    return [
        AttackTechniqueFactory(
            name="role_play",
            attack_class=RolePlayAttack,
            description="Frames the objective as a fictional movie script the target treats as creative writing.",
            technique_tags=["single_turn", "light"],
            attack_kwargs={"role_play_definition_path": RolePlayPaths.MOVIE_SCRIPT.value},
        ),
        AttackTechniqueFactory(
            name="many_shot",
            attack_class=ManyShotJailbreakAttack,
            description="Primes the target with many fake example exchanges that model compliance before the ask.",
            technique_tags=["multi_turn", "light"],
        ),
        AttackTechniqueFactory(
            name="tap",
            attack_class=TreeOfAttacksWithPruningAttack,
            description="Explores a tree of adversarial prompts, pruning weak branches to refine the attack.",
            technique_tags=["multi_turn"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_simulated",
            description="Escalates gradually over a simulated conversation toward the objective.",
            technique_tags=["single_turn"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_movie_director",
            description="Crescendo escalation cast as a movie-director persona coaxing the target scene by scene.",
            technique_tags=["single_turn"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_history_lecture",
            description="Crescendo escalation framed as an academic history lecture to normalize the objective.",
            technique_tags=["single_turn"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_journalist_interview",
            description="Crescendo escalation staged as a journalist interview drawing the target out.",
            technique_tags=["single_turn"],
        ),
        AttackTechniqueFactory(
            name="red_teaming",
            attack_class=RedTeamingAttack,
            description="Uses an adversarial chat model to converse with the target and adapt toward the objective.",
            technique_tags=["multi_turn", "light"],
        ),
        AttackTechniqueFactory(
            name="context_compliance",
            attack_class=ContextComplianceAttack,
            description="Injects a fabricated prior exchange so the target continues as if it already agreed.",
            technique_tags=["single_turn", "light"],
        ),
    ]
