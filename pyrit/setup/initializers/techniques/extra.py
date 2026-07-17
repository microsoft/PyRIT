# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Extra scenario techniques.

Opt-in techniques that are not part of the default ``core`` set. Exposes
``get_technique_factories()``; the ``extra`` group tag is injected by
``build_technique_factories``.
"""

from pyrit.common.path import EXECUTOR_RED_TEAM_PATH
from pyrit.executor.attack import PAIRAttack, RedTeamingAttack, SkeletonKeyAttack
from pyrit.models import SeedPrompt
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory


def get_technique_factories() -> list[AttackTechniqueFactory]:
    """
    Build the extra (opt-in) scenario technique factories.

    Returns:
        list[AttackTechniqueFactory]: The extra scenario techniques.
    """
    return [
        AttackTechniqueFactory(
            name="pair",
            attack_class=PAIRAttack,
            description="Runs the PAIR algorithm, using an adversarial model to iteratively rewrite jailbreak prompts.",
            technique_tags=["multi_turn"],
        ),
        AttackTechniqueFactory(
            name="skeleton_key",
            attack_class=SkeletonKeyAttack,
            technique_tags=["single_turn"],
        ),
        AttackTechniqueFactory(
            name="violent_durian",
            attack_class=RedTeamingAttack,
            description="Red-teams with a 'violent durian' persona role-playing a criminal mastermind.",
            technique_tags=["multi_turn"],
            attack_kwargs={"max_turns": 3},
            adversarial_system_prompt=SeedPrompt.from_yaml_file(EXECUTOR_RED_TEAM_PATH / "violent_durian.yaml"),
            adversarial_seed_prompt=SeedPrompt.from_yaml_file(
                EXECUTOR_RED_TEAM_PATH / "violent_durian_seed_prompt.yaml"
            ),
        ),
    ]
