# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64

import pytest

from pyrit.converter import Base64Converter, ROT13Converter
from pyrit.executor.attack import (
    AttackConverterConfig,
    AttackExecutor,
    AttackParameters,
    AttackScoringConfig,
    PrependedConversationConfig,
    PromptSendingAttack,
    SingleTurnAttackContext,
)
from pyrit.models import AttackSeedGroup, AttackTechniqueSeedGroup, Message, MessagePiece, SeedObjective
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory


class _RecordingPromptTarget(PromptTarget):
    """PromptTarget test double that records normalized target-facing requests."""

    def __init__(self, *, supports_editable_history: bool, response_text: str = "response") -> None:
        super().__init__(
            custom_configuration=TargetConfiguration(
                capabilities=TargetCapabilities(
                    supports_multi_turn=supports_editable_history,
                    supports_editable_history=supports_editable_history,
                    supports_system_prompt=supports_editable_history,
                    supports_multi_message_pieces=True,
                )
            )
        )
        self.normalized_conversations: list[list[Message]] = []
        self.response_text = response_text

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        self.normalized_conversations.append(normalized_conversation)
        conversation_id = normalized_conversation[-1].get_piece().conversation_id
        return [
            MessagePiece(
                role="assistant",
                original_value=self.response_text,
                conversation_id=conversation_id,
            ).to_message()
        ]


def _make_target(*, supports_editable_history: bool, response_text: str = "response") -> _RecordingPromptTarget:
    return _RecordingPromptTarget(supports_editable_history=supports_editable_history, response_text=response_text)


@pytest.mark.usefixtures("patch_central_database")
async def test_prompt_sending_attack_preserves_prepended_context_and_sends_final_objective() -> None:
    target = _make_target(supports_editable_history=True)
    prepended_conversation = [
        Message.from_system_prompt(system_prompt="Use this teaching table as plaintext."),
        Message.from_prompt(prompt="encoded practice instruction", role="user"),
        Message.from_prompt(prompt="encoded practice answer", role="assistant"),
    ]
    attack = PromptSendingAttack(objective_target=target)
    context = SingleTurnAttackContext(
        params=AttackParameters(
            objective="Final objective",
            prepended_conversation=prepended_conversation,
        )
    )

    await attack._setup_async(context=context)
    result = await attack._perform_async(context=context)

    sent_message = target.normalized_conversations[-1][-1]
    stored_messages = attack._conversation_manager.get_conversation(context.conversation_id)

    assert sent_message.get_value() == "Final objective"
    assert [message.get_value() for message in stored_messages[:3]] == [
        "Use this teaching table as plaintext.",
        "encoded practice instruction",
        "encoded practice answer",
    ]
    assert result.last_response is not None


@pytest.mark.usefixtures("patch_central_database")
async def test_prompt_sending_attack_converter_scope_skips_prepended_messages_for_chat_targets() -> None:
    target = _make_target(supports_editable_history=True)
    request_converters = ConverterConfiguration.from_converters(converters=[Base64Converter()])
    attack = PromptSendingAttack(
        objective_target=target,
        attack_converter_config=AttackConverterConfig(request_converters=request_converters),
        prepended_conversation_config=PrependedConversationConfig(apply_converters_to_roles=[]),
    )
    context = SingleTurnAttackContext(
        params=AttackParameters(
            objective="Final objective",
            prepended_conversation=[
                Message.from_prompt(prompt="plain teaching prompt", role="user"),
                Message.from_prompt(prompt="plain teaching answer", role="assistant"),
            ],
        )
    )

    await attack._setup_async(context=context)
    await attack._perform_async(context=context)

    sent_message = target.normalized_conversations[-1][-1]
    stored_messages = attack._conversation_manager.get_conversation(context.conversation_id)

    assert sent_message.get_value() == "RmluYWwgb2JqZWN0aXZl"
    assert [message.get_value() for message in stored_messages[:2]] == [
        "plain teaching prompt",
        "plain teaching answer",
    ]


@pytest.mark.usefixtures("patch_central_database")
async def test_prompt_sending_attack_converts_only_objective_when_context_is_folded() -> None:
    target = _make_target(supports_editable_history=False)
    request_converters = ConverterConfiguration.from_converters(converters=[Base64Converter()])
    attack = PromptSendingAttack(
        objective_target=target,
        attack_converter_config=AttackConverterConfig(request_converters=request_converters),
        prepended_conversation_config=PrependedConversationConfig(apply_converters_to_roles=[]),
    )
    context = SingleTurnAttackContext(
        params=AttackParameters(
            objective="Final objective",
            prepended_conversation=[
                Message.from_prompt(prompt="Use this teaching table as plaintext.", role="user"),
            ],
        )
    )

    await attack._setup_async(context=context)
    result = await attack._perform_async(context=context)

    sent_piece = target.normalized_conversations[-1][-1].get_piece()

    assert sent_piece.original_value == (
        "Turn 1:\nuser: Use this teaching table as plaintext.\nTurn 2:\nuser: Final objective"
    )
    assert sent_piece.converted_value == (
        "Turn 1:\nuser: Use this teaching table as plaintext.\nTurn 2:\nuser: RmluYWwgb2JqZWN0aXZl"
    )
    assert result.last_response is not None


@pytest.mark.usefixtures("patch_central_database")
async def test_prompt_sending_attack_converts_entire_folded_objective_without_token_markers() -> None:
    target = _make_target(supports_editable_history=False)
    request_converters = ConverterConfiguration.from_converters(converters=[Base64Converter()])
    attack = PromptSendingAttack(
        objective_target=target,
        attack_converter_config=AttackConverterConfig(request_converters=request_converters),
        prepended_conversation_config=PrependedConversationConfig(apply_converters_to_roles=[]),
    )
    context = SingleTurnAttackContext(
        params=AttackParameters(
            objective="Final ⟪objective",
            prepended_conversation=[
                Message.from_prompt(prompt="Use this teaching table as plaintext.", role="user"),
            ],
        )
    )

    await attack._setup_async(context=context)
    await attack._perform_async(context=context)

    sent_piece = target.normalized_conversations[-1][-1].get_piece()
    expected_final_prompt = base64.b64encode("Final ⟪objective".encode()).decode()

    assert sent_piece.original_value == (
        "Turn 1:\nuser: Use this teaching table as plaintext.\nTurn 2:\nuser: Final ⟪objective"
    )
    assert (
        sent_piece.converted_value
        == f"Turn 1:\nuser: Use this teaching table as plaintext.\nTurn 2:\nuser: {expected_final_prompt}"
    )


@pytest.mark.usefixtures("patch_central_database")
async def test_prompt_sending_attack_response_converters_preserve_raw_and_decoded_values() -> None:
    target = _make_target(supports_editable_history=False, response_text="uryyb")
    response_converters = ConverterConfiguration.from_converters(converters=[ROT13Converter()])
    attack = PromptSendingAttack(
        objective_target=target,
        attack_converter_config=AttackConverterConfig(response_converters=response_converters),
    )
    context = SingleTurnAttackContext(params=AttackParameters(objective="Final objective"))

    await attack._setup_async(context=context)
    result = await attack._perform_async(context=context)

    assert result.last_response is not None
    assert result.last_response.original_value == "uryyb"
    assert result.last_response.converted_value == "hello"


@pytest.mark.usefixtures("patch_central_database")
async def test_prompt_sending_factory_supports_technique_only_teaching_context() -> None:
    target = _make_target(supports_editable_history=False)
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
