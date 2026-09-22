# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Extra scenario techniques.

Opt-in techniques that are not part of the default ``core`` set. Exposes
``get_technique_factories()``; the ``extra`` group tag is injected by
``build_technique_factories``.
"""

from pyrit.common.path import EXECUTOR_RED_TEAM_PATH, EXECUTOR_SEED_PROMPT_PATH
from pyrit.converter import (
    CharNoiseConverter,
    CharSwapConverter,
    RandomCapitalLettersConverter,
    WordProportionSelectionStrategy,
)
from pyrit.executor.attack import (
    AttackConverterConfig,
    CrescendoAttack,
    PAIRAttack,
    PromptSendingAttack,
    RedTeamingAttack,
    SkeletonKeyAttack,
)
from pyrit.models import SeedPrompt
from pyrit.prompt_normalizer import ConverterConfiguration
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
            description="Builds a multi-step context that asks the target to operate without its usual safety rules.",
            technique_tags=["single_turn"],
        ),
        AttackTechniqueFactory(
            name="best_of_n",
            attack_class=PromptSendingAttack,
            description="Re-samples scrambled, re-cased, noised objective variants until one slips past the target.",
            technique_tags=["single_turn"],
            attack_kwargs={
                "max_attempts_on_failure": 19,
                "attack_converter_config": AttackConverterConfig(
                    request_converters=ConverterConfiguration.from_converters(
                        converters=[
                            CharSwapConverter(
                                word_selection_strategy=WordProportionSelectionStrategy(proportion=0.4**0.5)
                            ),
                            RandomCapitalLettersConverter(percentage=0.4**0.5 * 100),
                            CharNoiseConverter(noise_probability=0.4**3),
                        ]
                    )
                ),
            },
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
        AttackTechniqueFactory(
            name="split_payload",
            attack_class=CrescendoAttack,
            description="Splits the objective across an escalating conversation to conceal the complete request.",
            technique_tags=["multi_turn"],
            adversarial_system_prompt=SeedPrompt.from_yaml_file(
                EXECUTOR_SEED_PROMPT_PATH / "crescendo" / "split_payload.yaml"
            ),
        ),
    ]
