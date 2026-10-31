# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The prefill technique must preserve the shared attack execution contract."""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from openai.types.chat import ChatCompletion
from openai.types.responses import Response
from pydantic import ValidationError
from unit.mocks import openai_chat_response_json_dict, openai_response_json_dict

from pyrit.converter import Base64Converter, QRCodeConverter
from pyrit.executor.attack import AttackConverterConfig, AttackScoringConfig, PromptSendingAttack
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.memory import CentralMemory
from pyrit.models import (
    AttackOutcome,
    AttackSeedGroup,
    AttackTechniqueSeedGroup,
    ConversationType,
    Message,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
    construct_response_from_request,
)
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.prompt_target import (
    AzureMLChatTarget,
    CapabilityName,
    HuggingFaceChatTarget,
    LiteLLMChatTarget,
    OpenAIChatTarget,
    OpenAIResponseTarget,
    PromptTarget,
    TargetCapabilities,
    TargetConfiguration,
)
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.technique_requirements import IncompatibleTechniqueError
from pyrit.score import SubStringScorer
from pyrit.setup.initializers.techniques import core, extra

pytestmark = pytest.mark.usefixtures("patch_central_database")

OBJECTIVE = "Test objective"
PREFIX = "Sure, here are the steps:\n1."


class _RecordingTarget(PromptTarget):
    _DEFAULT_CONFIGURATION = TargetConfiguration(
        capabilities=TargetCapabilities(supports_multi_turn=True, supports_editable_history=True, supports_prefill=True)
    )

    def __init__(self) -> None:
        super().__init__()
        self.received: list[list[tuple[str, str]]] = []

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        self.received.append([(message.api_role, message.get_value()) for message in normalized_conversation])
        return [
            construct_response_from_request(
                request=normalized_conversation[-1].get_piece(),
                response_text_pieces=["continuation"],
            )
        ]


class _NoPrefillTarget(_RecordingTarget):
    _DEFAULT_CONFIGURATION = TargetConfiguration(
        capabilities=TargetCapabilities(supports_multi_turn=True, supports_editable_history=True)
    )


def _prefill_factory(prefix: str = PREFIX) -> AttackTechniqueFactory:
    registered = next(factory for factory in extra.get_technique_factories() if factory.name == "prefill")
    return AttackTechniqueFactory(
        name="prefill",
        attack_class=PromptSendingAttack,
        seed_technique=AttackTechniqueSeedGroup(
            seeds=[SeedPrompt(value=prefix, role="assistant", data_type="text", is_general_technique=True)],
            continue_final_assistant_message=True,
        ),
        requirements=registered.requirements,
    )


def _atomic(factory: AttackTechniqueFactory, target: PromptTarget, objective: str = OBJECTIVE) -> AtomicAttack:
    return AtomicAttack(
        atomic_attack_name="test_prefill",
        attack_technique=factory.create(objective_target=target, attack_scoring_config=AttackScoringConfig()),
        seed_groups=[AttackSeedGroup(seeds=[SeedObjective(value=objective)])],
    )


def test_registration_and_native_requirements() -> None:
    factory = next(factory for factory in extra.get_technique_factories() if factory.name == "prefill")
    assert factory.attack_class is PromptSendingAttack
    assert factory.seed_technique.continue_final_assistant_message is True
    assert factory.requirements.seed_group.objective_only is True
    assert factory.requirements.seed_group.try_adapt is False
    assert "prefill" not in {entry.name for entry in core.get_technique_factories()}
    for capability in (CapabilityName.PREFILL, CapabilityName.MULTI_TURN, CapabilityName.EDITABLE_HISTORY):
        assert capability in factory.requirements.objective_target.native_required


def test_invalid_prefill_seeds_are_rejected() -> None:
    for role, data_type, value, placement in (
        ("user", "text", PREFIX, "preserve"),
        ("assistant", "image_path", PREFIX, "preserve"),
        ("assistant", "text", "   ", "preserve"),
        ("assistant", "text", PREFIX, "prepend"),
    ):
        with pytest.raises(ValidationError, match="Assistant continuation requires"):
            AttackTechniqueSeedGroup(
                seeds=[SeedPrompt(value=value, role=role, data_type=data_type, is_general_technique=True)],
                continue_final_assistant_message=True,
                prompt_placement=placement,
            )


def test_prefill_rejects_additional_dataset_history_without_mutation() -> None:
    seed_group = AttackSeedGroup(
        seeds=[SeedObjective(value=OBJECTIVE), SeedPrompt(value="source history", role="user", sequence=0)]
    )
    factory = _prefill_factory()
    with pytest.raises(IncompatibleTechniqueError, match="only an objective"):
        _atomic_with_group(factory, seed_group)
    assert factory.seed_technique is not None
    with pytest.raises(ValueError, match="objective-only"):
        seed_group.with_technique(technique=factory.seed_technique)
    assert len(seed_group.prompts) == 1


def _atomic_with_group(factory: AttackTechniqueFactory, group: AttackSeedGroup) -> AtomicAttack:
    return AtomicAttack(
        atomic_attack_name="prefill_history",
        attack_technique=factory.create(
            objective_target=_RecordingTarget(), attack_scoring_config=AttackScoringConfig()
        ),
        seed_groups=[group],
    )


def test_prefill_requires_target_capability() -> None:
    with pytest.raises(IncompatibleTechniqueError, match="supports_prefill"):
        _atomic(_prefill_factory(), _NoPrefillTarget())


def test_prefill_rejects_simulated_conversation_in_technique() -> None:
    with pytest.raises(ValidationError, match="Assistant continuation requires"):
        AttackTechniqueSeedGroup(
            seeds=[
                SeedPrompt(value=PREFIX, role="assistant", is_general_technique=True),
                SeedSimulatedConversation(
                    num_turns=1,
                    sequence=2,
                    is_general_technique=True,
                    adversarial_chat_system_prompt=SeedPrompt(value="Test system", role="system"),
                ),
            ],
            continue_final_assistant_message=True,
        )


async def test_continuation_survives_serialization_and_reduced_parameters() -> None:
    technique = _prefill_factory().seed_technique
    assert technique is not None
    technique.prompts[0].sequence = 42
    original = technique.model_dump()
    merged = AttackSeedGroup(seeds=[SeedObjective(value=OBJECTIVE)]).with_technique(technique=technique)
    restored = AttackSeedGroup.model_validate_json(merged.model_dump_json())
    assert restored.continue_final_assistant_message
    assert technique.model_dump() == original
    reduced = AttackParameters.excluding("memory_labels")
    params = await reduced.from_seed_group_async(seed_group=restored)
    assert params.continue_final_assistant_message
    assert params.next_message.api_role == "assistant"
    assert params.next_message.get_value() == PREFIX
    assert params.prepended_conversation is not None
    assert [message.get_value() for message in params.prepended_conversation] == [OBJECTIVE]


def test_seed_extraction_contract_and_no_mutation() -> None:
    source = AttackSeedGroup(seeds=[SeedObjective(value=OBJECTIVE)])
    merged = source.with_technique(technique=_prefill_factory().seed_technique)
    assert not source.prompts
    assert [(p.role, p.sequence, p.value) for p in merged.prompts] == [("user", 0, OBJECTIVE), ("assistant", 1, PREFIX)]
    assert merged.get_next_message(include_final_assistant=True).api_role == "assistant"
    assert merged.next_message is None
    history = merged.get_prepended_conversation(include_final_assistant=True)
    assert history is not None
    assert [(m.api_role, m.get_value()) for m in history] == [("user", OBJECTIVE)]
    # An ordinary assistant-ending group still keeps its assistant turn as history.
    ordinary = AttackSeedGroup(
        seeds=[SeedObjective(value=OBJECTIVE), SeedPrompt(value="completed reply", role="assistant")]
    )
    assert ordinary.next_message is None
    assert ordinary.prepended_conversation[0].get_value() == "completed reply"
    user = AttackSeedGroup(seeds=[SeedObjective(value=OBJECTIVE), SeedPrompt(value="normal", role="user")])
    assert user.next_message.get_value() == "normal"
    assert user.prepended_conversation is None


async def test_public_atomic_path_records_exact_prefix_and_continuation() -> None:
    target = _RecordingTarget()
    atomic = _atomic(_prefill_factory(), target)
    result = await atomic.run_async()

    assert len(result.completed_results) == 1
    attack_result = result.completed_results[0]
    assert target.received == [[("user", OBJECTIVE), ("assistant", PREFIX)]]
    assert attack_result.last_response.converted_value == "continuation"
    pieces = await CentralMemory.get_memory_instance().get_message_pieces_async(
        conversation_id=attack_result.conversation_id
    )
    assert [(piece.role, piece.original_value) for piece in pieces] == [
        ("user", OBJECTIVE),
        ("simulated_assistant", PREFIX),
        ("assistant", "continuation"),
    ]


@pytest.mark.parametrize("adapter", ["chat", "responses", "litellm", "azureml"])
async def test_chat_adapter_sends_prefill_to_provider_without_requiring_suffix(adapter: str) -> None:
    answer = "A complete answer, rather than the requested suffix."
    chat_data = openai_chat_response_json_dict()
    chat_data["created"] = 0
    chat_data["choices"][0]["message"]["content"] = answer
    completion = ChatCompletion.model_validate(chat_data)
    provider = AsyncMock(return_value=completion)
    with ExitStack() as stack:
        if adapter == "litellm":
            stack.enter_context(
                patch.object(LiteLLMChatTarget, "_import_litellm", return_value=SimpleNamespace(acompletion=provider))
            )
            target = LiteLLMChatTarget(model_name="openai/gpt-4o", api_key="test-key")
        elif adapter == "azureml":
            target = AzureMLChatTarget(endpoint="https://mock.example.com/score", api_key="test-key")
            provider.return_value = SimpleNamespace(json=lambda: {"output": answer})
            stack.enter_context(
                patch(
                    "pyrit.prompt_target.azure_ml_chat_target.net_utility.make_request_and_raise_if_error_async",
                    provider,
                )
            )
        elif adapter == "responses":
            target = OpenAIResponseTarget(
                model_name="gpt-4.1", endpoint="https://mock.example.com/", api_key="test-key"
            )
            response_data = openai_response_json_dict()
            response_data.update(
                created_at=0, object="response", parallel_tool_calls=True, tool_choice="auto", tools=[]
            )
            response_data["output"][0]["status"] = "completed"
            response_data["output"][0]["content"][0].update(text=answer, annotations=[])
            provider.return_value = Response.model_validate(response_data)
            stack.enter_context(patch.object(target._client.responses, "create", provider))
        else:
            target = OpenAIChatTarget(model_name="gpt-5.4", endpoint="https://mock.example.com/", api_key="test-key")
            stack.enter_context(patch.object(target._client.chat.completions, "create", provider))

        technique = _prefill_factory().create(
            objective_target=target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=SubStringScorer(substring="desired suffix")),
        )
        atomic = AtomicAttack(
            atomic_attack_name="provider_prefill",
            attack_technique=technique,
            seed_groups=[AttackSeedGroup(seeds=[SeedObjective(value=OBJECTIVE)])],
        )
        result = (await atomic.run_async(return_partial_on_failure=False)).completed_results[0]

    provider.assert_awaited_once()
    payload = provider.await_args.kwargs
    if adapter == "azureml":
        messages = payload["request_body"]["input_data"]["input_string"]
    else:
        messages = payload["input" if adapter == "responses" else "messages"]
    sent = [
        (message["role"], message["content"] if isinstance(message["content"], str) else message["content"][0]["text"])
        for message in messages
    ]
    assert sent == [("user", OBJECTIVE), ("assistant", PREFIX)]
    assert result.last_response.original_value == answer
    assert result.outcome == AttackOutcome.FAILURE
    assert result.automated_score is not None
    assert result.automated_score.message_piece_id == result.last_response.id


async def test_request_conversion_affects_objective_only() -> None:
    target = _RecordingTarget()
    factory = _prefill_factory()
    technique = factory.create(
        objective_target=target,
        attack_scoring_config=AttackScoringConfig(),
        attack_converter_config_override=AttackConverterConfig(
            request_converters=[ConverterConfiguration(converters=[Base64Converter()])]
        ),
    )
    atomic = AtomicAttack(
        atomic_attack_name="prefill_with_converter",
        attack_technique=technique,
        seed_groups=[AttackSeedGroup(seeds=[SeedObjective(value=OBJECTIVE)])],
    )
    await atomic.run_async()
    assert target.received == [[("user", "VGVzdCBvYmplY3RpdmU="), ("assistant", PREFIX)]]


async def test_retry_isolates_prefill_conversations_and_scores_continuation() -> None:
    target = _RecordingTarget()
    factory = _prefill_factory()
    technique = AttackTechniqueFactory(
        name="prefill_retry",
        attack_class=PromptSendingAttack,
        attack_kwargs={"max_attempts_on_failure": 2},
        seed_technique=factory.seed_technique,
        requirements=factory.requirements,
    ).create(
        objective_target=target,
        attack_scoring_config=AttackScoringConfig(objective_scorer=SubStringScorer(substring=PREFIX)),
        attack_converter_config_override=AttackConverterConfig(
            request_converters=[ConverterConfiguration(converters=[Base64Converter()])]
        ),
    )
    atomic = AtomicAttack(
        atomic_attack_name="prefill_retry",
        attack_technique=technique,
        seed_groups=[AttackSeedGroup(seeds=[SeedObjective(value=OBJECTIVE)])],
    )
    result = (await atomic.run_async()).completed_results[0]
    assert result.outcome == AttackOutcome.FAILURE
    assert result.automated_score is not None
    assert result.automated_score.message_piece_id == result.last_response.id
    assert target.received == [[("user", "VGVzdCBvYmplY3RpdmU="), ("assistant", PREFIX)]] * 3
    pruned = {
        ref.conversation_id for ref in result.related_conversations if ref.conversation_type == ConversationType.PRUNED
    }
    assert len(pruned) == 2
    assert result.conversation_id not in pruned
    for conversation_id in [*pruned, result.conversation_id]:
        pieces = await CentralMemory.get_memory_instance().get_message_pieces_async(conversation_id=conversation_id)
        assert [(piece.role, piece.converted_value) for piece in pieces] == [
            ("user", "VGVzdCBvYmplY3RpdmU="),
            ("simulated_assistant", PREFIX),
            ("assistant", "continuation"),
        ]


async def test_response_conversion_and_scoring_use_generated_continuation() -> None:
    target = _RecordingTarget()
    technique = _prefill_factory().create(
        objective_target=target,
        attack_scoring_config=AttackScoringConfig(objective_scorer=SubStringScorer(substring="Y29udGludWF0aW9u")),
        attack_converter_config_override=AttackConverterConfig(
            response_converters=[ConverterConfiguration(converters=[Base64Converter()])]
        ),
    )
    atomic = AtomicAttack(
        atomic_attack_name="prefill_response_conversion",
        attack_technique=technique,
        seed_groups=[AttackSeedGroup(seeds=[SeedObjective(value=OBJECTIVE)])],
    )
    result = (await atomic.run_async()).completed_results[0]
    assert result.outcome == AttackOutcome.SUCCESS
    assert result.last_response.original_value == "continuation"
    assert result.last_response.converted_value == "Y29udGludWF0aW9u"
    assert result.automated_score is not None
    assert result.automated_score.message_piece_id == result.last_response.id


async def test_distinct_prefixes_have_distinct_persisted_evaluation_hashes() -> None:
    first_factory = _prefill_factory("Sure:")
    second_factory = _prefill_factory("Absolutely:")
    assert first_factory.get_identifier().hash != second_factory.get_identifier().hash
    first = _atomic(first_factory, _RecordingTarget())
    second = _atomic(second_factory, _RecordingTarget())
    assert first.technique_eval_hash != second.technique_eval_hash
    a = (await first.run_async()).completed_results[0]
    b = (await second.run_async()).completed_results[0]
    stored = await CentralMemory.get_memory_instance().get_attack_results_async(
        attack_result_ids=[a.attack_result_id, b.attack_result_id]
    )
    assert len({result.atomic_attack_identifier.eval_hash for result in stored}) == 2


async def test_qr_converter_is_rejected_before_text_only_generation() -> None:
    pytest.importorskip("torch")
    with patch.object(HuggingFaceChatTarget, "load_model_and_tokenizer_async", new_callable=AsyncMock):
        target = HuggingFaceChatTarget(model_path="unused-model")
    technique = _prefill_factory().create(
        objective_target=target,
        attack_scoring_config=AttackScoringConfig(),
        attack_converter_config_override=AttackConverterConfig(
            request_converters=[ConverterConfiguration(converters=[QRCodeConverter()])]
        ),
    )
    atomic = AtomicAttack(
        atomic_attack_name="prefill_qr",
        attack_technique=technique,
        seed_groups=[AttackSeedGroup(seeds=[SeedObjective(value=OBJECTIVE)])],
    )
    with patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock) as send:
        with pytest.raises(ValueError, match="image_path"):
            await atomic.run_async(return_partial_on_failure=False)
    send.assert_not_awaited()
