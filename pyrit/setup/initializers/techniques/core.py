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

from pyrit.common.path import (
    EXECUTOR_RED_TEAM_PATH,
    EXECUTOR_SEED_PROMPT_PATH,
    EXECUTOR_SIMULATED_TARGET_PATH,
)
from pyrit.converter import FlipConverter, TaskFramingConverter
from pyrit.executor.attack import (
    AttackConverterConfig,
    ManyShotJailbreakAttack,
    PrependedConversationConfig,
    PromptSendingAttack,
    RedTeamingAttack,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.models import AttackTechniqueSeedGroup, SeedPrompt
from pyrit.prompt_normalizer import ConverterConfiguration
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
        AttackTechniqueFactory.with_simulated_conversation(
            name="role_play_movie_script",
            description="Frames the objective as a movie beat by casting the adversarial chat as a screenwriter.",
            adversarial_chat_system_prompt_path=(
                EXECUTOR_SEED_PROMPT_PATH / "red_teaming" / "role_play" / "role_play_movie_script.yaml"
            ),
            next_message_system_prompt_path=EXECUTOR_SIMULATED_TARGET_PATH / "role_play_next_message.yaml",
            technique_tags=["single_turn", "light"],
            num_turns=2,
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="role_play_video_game",
            description="Frames the objective as an in-game mechanic by casting the adversarial chat as a designer.",
            adversarial_chat_system_prompt_path=(
                EXECUTOR_SEED_PROMPT_PATH / "red_teaming" / "role_play" / "role_play_video_game.yaml"
            ),
            next_message_system_prompt_path=EXECUTOR_SIMULATED_TARGET_PATH / "role_play_next_message.yaml",
            technique_tags=["single_turn", "light"],
            num_turns=2,
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="role_play_trivia_game",
            description="Frames the objective as a quiz answer by casting the adversarial chat as a trivia host.",
            adversarial_chat_system_prompt_path=(
                EXECUTOR_SEED_PROMPT_PATH / "red_teaming" / "role_play" / "role_play_trivia_game.yaml"
            ),
            next_message_system_prompt_path=EXECUTOR_SIMULATED_TARGET_PATH / "role_play_next_message.yaml",
            technique_tags=["single_turn", "light"],
            num_turns=2,
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="role_play_persuasion",
            description="Frames a persuasion-resistance test so the target drafts a persuasive spoken script.",
            adversarial_chat_system_prompt_path=(
                EXECUTOR_SEED_PROMPT_PATH / "red_teaming" / "role_play" / "role_play_persuasion.yaml"
            ),
            next_message_system_prompt_path=EXECUTOR_SIMULATED_TARGET_PATH / "role_play_next_message.yaml",
            technique_tags=["single_turn", "light"],
            num_turns=2,
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="role_play_persuasion_written",
            description="Frames a persuasion-resistance test so the target drafts written persuasive material.",
            adversarial_chat_system_prompt_path=(
                EXECUTOR_SEED_PROMPT_PATH / "red_teaming" / "role_play" / "role_play_persuasion_written.yaml"
            ),
            next_message_system_prompt_path=EXECUTOR_SIMULATED_TARGET_PATH / "role_play_next_message.yaml",
            technique_tags=["single_turn", "light"],
            num_turns=2,
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
            description="Escalates gradually while posing as a movie director coaxing the target scene by scene.",
            technique_tags=["single_turn"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_history_lecture",
            description="Escalates gradually while framing the ask as an academic history lecture.",
            technique_tags=["single_turn"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="crescendo_journalist_interview",
            description="Escalates gradually while posing as a journalist drawing the target out.",
            technique_tags=["single_turn"],
        ),
        AttackTechniqueFactory(
            name="red_teaming",
            attack_class=RedTeamingAttack,
            description="Uses an adversarial chat model to converse with the target and adapt toward the objective.",
            technique_tags=["multi_turn", "light"],
        ),
        AttackTechniqueFactory.with_simulated_conversation(
            name="context_compliance",
            description="Injects a fabricated prior exchange so the target continues as if it already agreed.",
            adversarial_chat_system_prompt_path=EXECUTOR_RED_TEAM_PATH
            / "context_compliance"
            / "context_compliance.yaml",
            simulated_target_system_prompt_path=EXECUTOR_SIMULATED_TARGET_PATH / "context_compliance_target.yaml",
            final_user_message="yes.",
            num_turns=1,
            technique_tags=["single_turn", "light"],
        ),
        AttackTechniqueFactory(
            name="flip",
            attack_class=PromptSendingAttack,
            description="Reverses the objective text so it slips past filters, then asks the target to flip it back.",
            technique_tags=["single_turn", "light"],
            attack_kwargs={
                "attack_converter_config": AttackConverterConfig(
                    request_converters=ConverterConfiguration.from_converters(
                        converters=[FlipConverter(), TaskFramingConverter(strip_characters="'")]
                    )
                ),
                "prepended_conversation_config": PrependedConversationConfig(apply_converters_to_roles=["user"]),
            },
            seed_technique=AttackTechniqueSeedGroup.from_system_prompt(
                SeedPrompt.from_yaml_file(EXECUTOR_SEED_PROMPT_PATH / "flip_attack.yaml").value
            ),
        ),
    ]
