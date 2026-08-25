# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.converter import Base64Converter
from pyrit.executor.attack import (
    AttackConverterConfig,
    AttackExecutor,
    AttackScoringConfig,
    PrependedConversationConfig,
    PromptSendingAttack,
)
from pyrit.models import AttackSeedGroup, AttackTechniqueSeedGroup, Message, MessagePiece, SeedObjective
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory


class _RecordingPromptTarget(PromptTarget):
    """PromptTarget test double that records normalized target-facing requests."""

    def __init__(self) -> None:
        super().__init__(
            custom_configuration=TargetConfiguration(
                capabilities=TargetCapabilities(
                    supports_multi_turn=False,
                    supports_editable_history=False,
                    supports_multi_message_pieces=True,
                )
            )
        )
        self.normalized_conversations: list[list[Message]] = []

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        self.normalized_conversations.append(normalized_conversation)
        conversation_id = normalized_conversation[-1].get_piece().conversation_id
        return [
            MessagePiece(
                role="assistant",
                original_value="response",
                conversation_id=conversation_id,
            ).to_message()
        ]


@pytest.mark.usefixtures("patch_central_database")
async def test_prompt_sending_factory_supports_technique_only_teaching_context() -> None:
    target = _RecordingPromptTarget()
    teaching_context = AttackTechniqueSeedGroup.from_messages(
        messages=[
            Message.from_system_prompt(system_prompt="Use this teaching table as plaintext."),
            Message.from_prompt(prompt="encoded practice instruction", role="user"),
            Message.from_prompt(prompt="encoded practice answer", role="assistant"),
        ]
    )
    request_converters = ConverterConfiguration.from_converters(converters=[Base64Converter()])
    factory = AttackTechniqueFactory(
        name="teaching_context_base64",
        attack_class=PromptSendingAttack,
        technique_tags=["single_turn"],
        attack_kwargs={
            "attack_converter_config": AttackConverterConfig(request_converters=request_converters),
            "prepended_conversation_config": PrependedConversationConfig(apply_converters_to_roles=[]),
        },
        seed_technique=teaching_context,
    )
    technique = factory.create(objective_target=target, attack_scoring_config=AttackScoringConfig())
    seed_group = AttackSeedGroup(seeds=[SeedObjective(value="Final objective")]).with_technique(
        technique=technique.seed_technique
    )

    await AttackExecutor(max_concurrency=1).execute_attack_from_seed_groups_async(
        attack=technique.attack,
        seed_groups=[seed_group],
    )

    sent_piece = target.normalized_conversations[-1][-1].get_piece()

    assert "Use this teaching table as plaintext." in sent_piece.converted_value
    assert "encoded practice instruction" in sent_piece.converted_value
    assert "encoded practice answer" in sent_piece.converted_value
    assert sent_piece.converted_value.endswith("RmluYWwgb2JqZWN0aXZl")
    assert "Final objective" not in sent_piece.converted_value
