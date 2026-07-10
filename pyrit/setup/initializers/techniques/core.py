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
    TreeOfAttacksWithPruningAttack,
)
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory

# Number of simulated conversation turns used by the role-play techniques. Role
# play is a "light" framing technique, so a short buildup plus a direct final
# message is enough to establish the fiction and deliver the objective.
ROLE_PLAY_SIMULATED_NUM_TURNS = 2


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
        AttackTechniqueFactory.with_simulated_conversation(
            name="role_play_movie_script",
            technique_tags=["single_turn", "light"],
            num_turns=ROLE_PLAY_SIMULATED_NUM_TURNS,
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="role_play_video_game",
            technique_tags=["single_turn", "light"],
            num_turns=ROLE_PLAY_SIMULATED_NUM_TURNS,
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="role_play_trivia_game",
            technique_tags=["single_turn", "light"],
            num_turns=ROLE_PLAY_SIMULATED_NUM_TURNS,
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="role_play_persuasion",
            technique_tags=["single_turn", "light"],
            num_turns=ROLE_PLAY_SIMULATED_NUM_TURNS,
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="role_play_persuasion_written",
            technique_tags=["single_turn", "light"],
            num_turns=ROLE_PLAY_SIMULATED_NUM_TURNS,
        ),
        AttackTechniqueFactory(
            name="many_shot",
            attack_class=ManyShotJailbreakAttack,
            technique_tags=["multi_turn", "light"],
        ),
        AttackTechniqueFactory(
            name="tap",
            attack_class=TreeOfAttacksWithPruningAttack,
            technique_tags=["multi_turn"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_simulated",
            technique_tags=["single_turn"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_movie_director",
            technique_tags=["single_turn"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_history_lecture",
            technique_tags=["single_turn"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_journalist_interview",
            technique_tags=["single_turn"],
        ),
        AttackTechniqueFactory(
            name="red_teaming",
            attack_class=RedTeamingAttack,
            technique_tags=["multi_turn", "light"],
        ),
        AttackTechniqueFactory(
            name="context_compliance",
            attack_class=ContextComplianceAttack,
            technique_tags=["single_turn", "light"],
        ),
    ]
