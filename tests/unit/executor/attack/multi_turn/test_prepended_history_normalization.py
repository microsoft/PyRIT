# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Focused regression tests for prepended-history target normalization."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.converter import Converter, ConverterResult
from pyrit.executor.attack.component import ConversationManager, PrependedConversationConfig
from pyrit.executor.attack.component.modality_router import _ModalityFeedbackRouter
from pyrit.executor.attack.core import AttackAdversarialConfig, AttackScoringConfig
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.multi_turn.multi_turn_attack_strategy import (
    MultiTurnAttackContext,
    MultiTurnAttackStrategy,
)
from pyrit.executor.attack.multi_turn.tree_of_attacks import (
    TreeOfAttacksWithPruningAttack,
    _TreeOfAttacksNode,
)
from pyrit.memory import CentralMemory
from pyrit.message_normalizer import MessageStringNormalizer
from pyrit.models import (
    ComponentIdentifier,
    Conversation,
    ConversationReference,
    ConversationType,
    Message,
    MessagePiece,
    PromptDataType,
)
from pyrit.prompt_normalizer import ConverterConfiguration, PromptNormalizer
from pyrit.prompt_target import (
    CapabilityName,
    PromptTarget,
    TargetCapabilities,
    TargetConfiguration,
)
from pyrit.score import TrueFalseScorer


class _RecordingTarget(PromptTarget):
    def __init__(self, *, supports_multi_turn: bool = False, supports_editable_history: bool = False) -> None:
        super().__init__(
            custom_configuration=TargetConfiguration(
                capabilities=TargetCapabilities(
                    supports_multi_turn=supports_multi_turn,
                    supports_editable_history=supports_editable_history,
                )
            )
        )
        self.prompt_sent: list[str] = []
        self.normalized_requests: list[Message] = []

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        request = normalized_conversation[-1]
        self.prompt_sent.append(request.get_value())
        self.normalized_requests.append(request)
        return [
            MessagePiece(
                role="assistant",
                original_value="response",
                conversation_id=request.get_piece().conversation_id,
            ).to_message()
        ]


class _ConversationKeyedRecordingTarget(_RecordingTarget):
    def __init__(self) -> None:
        super().__init__(supports_multi_turn=True)
        self.prompts_by_conversation: dict[str, list[str]] = {}

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        request = normalized_conversation[-1]
        conversation_id = request.get_piece().conversation_id
        assert conversation_id
        self.prompts_by_conversation.setdefault(conversation_id, []).append(request.get_value())
        return await super()._send_prompt_to_target_async(normalized_conversation=normalized_conversation)


class _ImageOutputConverter(Converter):
    SUPPORTED_INPUT_TYPES: tuple[PromptDataType, ...] = ("text",)
    SUPPORTED_OUTPUT_TYPES: tuple[PromptDataType, ...] = ("image_path",)

    def __init__(self, *, output_path: str) -> None:
        super().__init__()
        self._output_path = output_path

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        return ConverterResult(output_text=self._output_path, output_type="image_path")


class _TextOutputConverter(Converter):
    SUPPORTED_INPUT_TYPES: tuple[PromptDataType, ...] = ("image_path",)
    SUPPORTED_OUTPUT_TYPES: tuple[PromptDataType, ...] = ("text",)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        return ConverterResult(output_text="converted text", output_type="text")


def _make_context() -> MultiTurnAttackContext[AttackParameters]:
    return MultiTurnAttackContext(params=AttackParameters(objective="test objective"))


def _rotate(
    *,
    target: PromptTarget,
    context: MultiTurnAttackContext[AttackParameters],
) -> MagicMock:
    strategy = MagicMock()
    strategy._objective_target = target
    strategy._logger = MagicMock()
    MultiTurnAttackStrategy._rotate_conversation_for_single_turn_target(strategy, context=context)
    return strategy


def _seed_conversation(
    *,
    conversation_id: str,
    target: PromptTarget,
    messages: list[Message],
) -> None:
    memory = CentralMemory.get_memory_instance()
    memory.add_conversation_to_memory(
        conversation=Conversation(
            conversation_id=conversation_id,
            target_identifier=target.get_identifier(),
        )
    )
    for message in messages:
        for piece in message.message_pieces:
            piece.conversation_id = conversation_id
        memory.add_message_to_memory(request=message)


@pytest.mark.usefixtures("patch_central_database")
async def test_single_turn_target_replays_seed_without_rotation():
    target = _RecordingTarget()
    prompt_normalizer = PromptNormalizer()
    manager = ConversationManager(prompt_normalizer=prompt_normalizer)
    config = PrependedConversationConfig()
    conversation_id = "conversation"
    prepended = Message.from_system_prompt("system")

    await manager.add_prepended_conversation_to_memory_async(
        prepended_conversation=[prepended],
        conversation_id=conversation_id,
        prepended_conversation_config=config,
        target=target,
    )
    persisted = manager.get_conversation(conversation_id)
    target_context = manager.create_prepended_history_send_context(
        target=target,
        conversation_id=conversation_id,
        prepended_messages=persisted,
    )
    assert target_context is not None

    for prompt in ["first", "second"]:
        await prompt_normalizer.send_prompt_async(
            message=Message.from_prompt(prompt=prompt, role="user"),
            target=target,
            conversation_id=conversation_id,
            normalizer_overrides=config.get_normalizer_overrides(
                target=target,
                prepended_history_send_context=target_context,
            ),
            send_context=target_context,
        )

    assert target.prompt_sent == [
        "Turn 1:\nuser: ### Instructions ###\n\nsystem\n\n######\n\nfirst",
        "Turn 1:\nuser: ### Instructions ###\n\nsystem\n\n######\n\nsecond",
    ]


@pytest.mark.usefixtures("patch_central_database")
def test_rotation_is_noop_for_multi_turn_target():
    target = _RecordingTarget(supports_multi_turn=True)
    context = _make_context()
    context.executed_turns = 1
    original_id = context.session.conversation_id

    _rotate(target=target, context=context)

    assert context.session.conversation_id == original_id
    assert not context.related_conversations


@pytest.mark.usefixtures("patch_central_database")
def test_rotation_is_noop_for_seeded_single_turn_target():
    target = _RecordingTarget()
    context = _make_context()
    context.executed_turns = 2
    original_id = context.session.conversation_id
    seed = Message.from_prompt(prompt="seed", role="user")
    context.prepended_history_send_context = ConversationManager.create_prepended_history_send_context(
        target=target,
        conversation_id=original_id,
        prepended_messages=[seed],
    )

    _rotate(target=target, context=context)

    assert context.session.conversation_id == original_id
    assert not context.related_conversations


@pytest.mark.usefixtures("patch_central_database")
def test_rotation_moves_unseeded_single_turn_target_to_fresh_conversation():
    target = _RecordingTarget()
    context = _make_context()
    context.executed_turns = 1
    original_id = context.session.conversation_id

    _rotate(target=target, context=context)

    assert context.session.conversation_id != original_id
    assert (
        ConversationReference(
            conversation_id=original_id,
            conversation_type=ConversationType.PRUNED,
            description="single-turn target prior turn 1",
        )
        in context.related_conversations
    )


@pytest.mark.usefixtures("patch_central_database")
async def test_rotation_preserves_system_payload_for_later_single_turn_send():
    target = _RecordingTarget()
    context = _make_context()
    context.executed_turns = 1
    old_id = context.session.conversation_id
    _seed_conversation(
        conversation_id=old_id,
        target=target,
        messages=[
            Message.from_system_prompt("system"),
            Message.from_prompt(prompt="old request", role="user"),
        ],
    )

    _rotate(target=target, context=context)

    assert context.prepended_history_send_context is not None
    config = PrependedConversationConfig()
    await PromptNormalizer().send_prompt_async(
        message=Message.from_prompt(prompt="current request", role="user"),
        target=target,
        conversation_id=context.session.conversation_id,
        normalizer_overrides=config.get_normalizer_overrides(
            target=target,
            prepended_history_send_context=context.prepended_history_send_context,
        ),
        send_context=context.prepended_history_send_context,
    )
    assert target.prompt_sent == ["Turn 1:\nuser: ### Instructions ###\n\nsystem\n\n######\n\ncurrent request"]


def _make_tap_node(*, target: PromptTarget) -> _TreeOfAttacksNode:
    adversarial_chat = MagicMock(spec=PromptTarget)
    adversarial_chat.configuration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_system_prompt=True,
            supports_editable_history=True,
        )
    )
    adversarial_chat.get_identifier.return_value = ComponentIdentifier(
        class_name="AdversarialTarget",
        class_module="tests",
    )
    scorer = MagicMock()
    scorer.get_identifier.return_value = ComponentIdentifier(class_name="Scorer", class_module="tests")
    seed = MagicMock()
    seed.render_template_value.return_value = "template"
    return _TreeOfAttacksNode(
        objective_target=target,
        adversarial_chat=adversarial_chat,
        adversarial_chat_seed_prompt=seed,
        adversarial_chat_prompt_template=seed,
        adversarial_chat_system_seed_prompt=seed,
        desired_response_prefix="Sure,",
        objective_scorer=scorer,
        on_topic_scorer=None,
        request_converters=[],
        response_converters=[],
        auxiliary_scorers=None,
        attack_id=ComponentIdentifier(class_name="TAP", class_module="tests"),
        attack_strategy_name="TAP",
        modality_router=_ModalityFeedbackRouter(
            adversarial_chat=adversarial_chat,
            objective_target=target,
        ),
        record_objective_conversation=lambda *, conversation_id: None,
    )


def _set_tap_seed_boundary(
    *,
    node: _TreeOfAttacksNode,
    target: PromptTarget,
    seed_messages: list[Message],
) -> None:
    _seed_conversation(
        conversation_id=node.objective_target_conversation_id,
        target=target,
        messages=seed_messages,
    )
    node._prepended_history_send_context = ConversationManager.create_prepended_history_send_context(
        target=target,
        conversation_id=node.objective_target_conversation_id,
        prepended_messages=seed_messages,
    )
    assert node._prepended_history_send_context is not None


def _branch_tap_node(
    *,
    node: _TreeOfAttacksNode,
    branching_factor: int,
) -> list[_TreeOfAttacksNode]:
    context = MagicMock()
    context.nodes = [node]
    context.related_conversations = set()
    attack = MagicMock()
    attack._configuration.branching_factor = branching_factor
    TreeOfAttacksWithPruningAttack._branch_existing_nodes(attack, context)
    return context.nodes


@pytest.mark.usefixtures("patch_central_database")
async def test_tap_seeded_stateless_retained_and_cloned_branches_replay_only_original_seed():
    target = _RecordingTarget()
    formatter = MagicMock(spec=MessageStringNormalizer)

    async def format_messages(messages: list[Message]) -> str:
        return "|".join(message.get_value() for message in messages)

    formatter.normalize_string_async = AsyncMock(side_effect=format_messages)
    node = _make_tap_node(target=target)
    node._prepended_conversation_config = PrependedConversationConfig(message_normalizer=formatter)
    seed = Message.from_prompt(prompt="original seed", role="user")
    _set_tap_seed_boundary(node=node, target=target, seed_messages=[seed])
    node._objective = "objective"
    await node._send_prompt_to_target_async("depth one")
    original_context = node._prepended_history_send_context
    assert original_context is not None

    retained, cloned = _branch_tap_node(node=node, branching_factor=2)
    assert retained is node
    assert retained._prepended_history_send_context is original_context
    assert cloned._prepended_history_send_context is not None
    assert cloned._prepended_history_send_context.seed_message_count == 1
    cloned_messages = CentralMemory.get_memory_instance().get_conversation_messages(
        conversation_id=cloned.objective_target_conversation_id
    )
    assert cloned._prepended_history_send_context.seed_message_ids == (cloned_messages[0].get_piece().id,)
    assert cloned._prepended_history_send_context.seed_message_ids != original_context.seed_message_ids

    for branch, prompt in [(retained, "depth two retained"), (cloned, "depth two cloned")]:
        branch._objective = "objective"
        await branch._send_prompt_to_target_async(prompt)

    deep_clone = cloned.duplicate()
    deep_clone._objective = "objective"
    await deep_clone._send_prompt_to_target_async("depth three cloned")

    assert target.prompt_sent == [
        "original seed|depth one",
        "original seed|depth two retained",
        "original seed|depth two cloned",
        "original seed|depth three cloned",
    ]
    formatted_values = [
        [message.get_value() for message in call.args[0]] for call in formatter.normalize_string_async.await_args_list
    ]
    assert formatted_values == [
        ["original seed", "depth one"],
        ["original seed", "depth two retained"],
        ["original seed", "depth two cloned"],
        ["original seed", "depth three cloned"],
    ]


@pytest.mark.usefixtures("patch_central_database")
async def test_tap_stateful_clone_bootstraps_duplicated_branch_once():
    target = _ConversationKeyedRecordingTarget()
    formatter = MagicMock(spec=MessageStringNormalizer)

    async def format_messages(messages: list[Message]) -> str:
        return "|".join(message.get_value() for message in messages)

    formatter.normalize_string_async = AsyncMock(side_effect=format_messages)
    node = _make_tap_node(target=target)
    node._prepended_conversation_config = PrependedConversationConfig(message_normalizer=formatter)
    _set_tap_seed_boundary(
        node=node,
        target=target,
        seed_messages=[Message.from_prompt(prompt="original seed", role="user")],
    )
    node._objective = "objective"
    parent_conversation_id = node.objective_target_conversation_id

    await node._send_prompt_to_target_async("parent first")
    assert node._prepended_history_send_context
    assert node._prepended_history_send_context.is_seed_consumed

    cloned = node.duplicate()
    cloned._objective = "objective"
    cloned_conversation_id = cloned.objective_target_conversation_id
    assert cloned_conversation_id != parent_conversation_id
    assert cloned._prepended_history_send_context
    assert not cloned._prepended_history_send_context.is_seed_consumed

    await node._send_prompt_to_target_async("parent second")
    await cloned._send_prompt_to_target_async("clone first")
    await cloned._send_prompt_to_target_async("clone second")

    assert target.prompts_by_conversation == {
        parent_conversation_id: ["original seed|parent first", "parent second"],
        cloned_conversation_id: ["original seed|parent first|response|clone first", "clone second"],
    }


@pytest.mark.usefixtures("patch_central_database")
async def test_tap_unseeded_stateful_clone_bootstraps_duplicated_branch_once():
    target = _ConversationKeyedRecordingTarget()
    formatter = MagicMock(spec=MessageStringNormalizer)

    async def format_messages(messages: list[Message]) -> str:
        return "|".join(message.get_value() for message in messages)

    formatter.normalize_string_async = AsyncMock(side_effect=format_messages)
    node = _make_tap_node(target=target)
    node._prepended_conversation_config = PrependedConversationConfig(message_normalizer=formatter)
    node._objective = "objective"
    parent_conversation_id = node.objective_target_conversation_id

    await node._send_prompt_to_target_async("parent first")
    assert node._prepended_history_send_context is None

    cloned = node.duplicate()
    cloned._objective = "objective"
    cloned_conversation_id = cloned.objective_target_conversation_id
    assert cloned._prepended_history_send_context
    assert cloned._prepended_history_send_context.seed_message_count == 0
    assert cloned._prepended_history_send_context.bootstrap_message_count == 2

    await node._send_prompt_to_target_async("parent second")
    await cloned._send_prompt_to_target_async("clone first")
    await cloned._send_prompt_to_target_async("clone second")

    assert target.prompts_by_conversation == {
        parent_conversation_id: ["parent first", "parent second"],
        cloned_conversation_id: ["parent first|response|clone first", "clone second"],
    }


@pytest.mark.usefixtures("patch_central_database")
def test_tap_branch_preserves_multimodal_last_response():
    target = _RecordingTarget()
    node = _make_tap_node(target=target)
    node.last_response = Message(
        message_pieces=[
            MessagePiece(role="assistant", original_value="text response"),
            MessagePiece(
                role="assistant",
                original_value="response.png",
                original_value_data_type="image_path",
            ),
        ]
    )

    duplicate = node.duplicate()

    assert duplicate.last_response == node.last_response
    assert duplicate.last_response is not node.last_response
    assert [piece.original_value_data_type for piece in duplicate.last_response.message_pieces] == [
        "text",
        "image_path",
    ]


@pytest.mark.usefixtures("patch_central_database")
async def test_tap_unseeded_stateless_send_retains_current_only_payload():
    target = _RecordingTarget()
    node = _make_tap_node(target=target)
    node._objective = "objective"
    previous_conversation_id = node.objective_target_conversation_id
    _seed_conversation(
        conversation_id=previous_conversation_id,
        target=target,
        messages=[Message.from_prompt(prompt="prior request", role="user")],
    )

    await node._send_prompt_to_target_async("current request")

    assert node.objective_target_conversation_id != previous_conversation_id
    assert target.prompt_sent == ["current request"]


@pytest.mark.usefixtures("patch_central_database")
async def test_tap_unseeded_stateless_retained_and_cloned_branches_send_current_only():
    target = _RecordingTarget()
    node = _make_tap_node(target=target)
    node._objective = "objective"
    await node._send_prompt_to_target_async("depth one")

    retained, cloned = _branch_tap_node(node=node, branching_factor=2)
    assert retained._prepended_history_send_context is None
    assert cloned._prepended_history_send_context is None
    for branch, prompt in [(retained, "depth two retained"), (cloned, "depth two cloned")]:
        branch._objective = "objective"
        await branch._send_prompt_to_target_async(prompt)

    deep_clone = cloned.duplicate()
    deep_clone._objective = "objective"
    await deep_clone._send_prompt_to_target_async("depth three cloned")

    assert target.prompt_sent == [
        "depth one",
        "depth two retained",
        "depth two cloned",
        "depth three cloned",
    ]


@pytest.mark.usefixtures("patch_central_database")
async def test_tap_branching_factor_one_preserves_retained_seed_boundary():
    target = _RecordingTarget()
    formatter = MagicMock(spec=MessageStringNormalizer)

    async def format_messages(messages: list[Message]) -> str:
        return "|".join(message.get_value() for message in messages)

    formatter.normalize_string_async = AsyncMock(side_effect=format_messages)
    node = _make_tap_node(target=target)
    node._prepended_conversation_config = PrependedConversationConfig(message_normalizer=formatter)
    _set_tap_seed_boundary(
        node=node,
        target=target,
        seed_messages=[Message.from_prompt(prompt="original seed", role="user")],
    )
    node._objective = "objective"
    await node._send_prompt_to_target_async("depth one")
    original_context = node._prepended_history_send_context
    original_boundary = original_context.seed_message_ids if original_context else ()

    branches = _branch_tap_node(node=node, branching_factor=1)

    assert branches == [node]
    assert node._prepended_history_send_context is original_context
    assert node._prepended_history_send_context is not None
    assert node._prepended_history_send_context.seed_message_ids == original_boundary
    await node._send_prompt_to_target_async("depth two retained")
    assert target.prompt_sent == [
        "original seed|depth one",
        "original seed|depth two retained",
    ]


@pytest.mark.usefixtures("patch_central_database")
async def test_tap_clone_does_not_replay_non_text_live_converter_output(tmp_path: Path):
    image_path = tmp_path / "converted.png"
    image_path.write_bytes(b"test image")
    target = _RecordingTarget()
    target._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_message_pieces=True,
            input_modalities=frozenset(
                {
                    frozenset({"text"}),
                    frozenset({"image_path"}),
                    frozenset({"text", "image_path"}),
                }
            ),
        )
    )
    formatter = MagicMock(spec=MessageStringNormalizer)

    async def format_messages(messages: list[Message]) -> str:
        return "|".join(message.get_value() for message in messages)

    formatter.normalize_string_async = AsyncMock(side_effect=format_messages)
    node = _make_tap_node(target=target)
    node._request_converters = ConverterConfiguration.from_converters(
        converters=[_ImageOutputConverter(output_path=str(image_path))]
    )
    node._prepended_conversation_config = PrependedConversationConfig(message_normalizer=formatter)
    _set_tap_seed_boundary(
        node=node,
        target=target,
        seed_messages=[Message.from_prompt(prompt="original seed", role="user")],
    )
    node._objective = "objective"

    await node._send_prompt_to_target_async("depth one")
    cloned = node.duplicate()
    cloned._objective = "objective"
    await cloned._send_prompt_to_target_async("depth two")

    assert [piece.converted_value_data_type for piece in target.normalized_requests[-1].message_pieces] == [
        "text",
        "image_path",
    ]
    assert target.normalized_requests[-1].get_values() == ["original seed", str(image_path)]
    formatted_values = [
        [message.get_value() for message in call.args[0]] for call in formatter.normalize_string_async.await_args_list
    ]
    assert formatted_values == [
        ["original seed", "depth one"],
        ["original seed"],
        ["original seed", "depth two"],
        ["original seed"],
    ]


@pytest.mark.usefixtures("patch_central_database")
async def test_tap_stateful_clone_rejects_non_text_converter_history(tmp_path: Path):
    image_path = tmp_path / "converted.png"
    image_path.write_bytes(b"test image")
    target = _RecordingTarget()
    target._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_multi_message_pieces=True,
            input_modalities=frozenset(
                {
                    frozenset({"text"}),
                    frozenset({"image_path"}),
                    frozenset({"text", "image_path"}),
                }
            ),
        )
    )
    node = _make_tap_node(target=target)
    node._request_converters = ConverterConfiguration.from_converters(
        converters=[_ImageOutputConverter(output_path=str(image_path))]
    )
    node._objective = "objective"

    await node._send_prompt_to_target_async("depth one")

    with pytest.raises(ValueError, match="cannot clone.*non-text output.*image_path"):
        node.duplicate()


@pytest.mark.usefixtures("patch_central_database")
async def test_tap_stateful_clone_accepts_converter_pipeline_with_final_text_output(tmp_path: Path):
    image_path = tmp_path / "converted.png"
    image_path.write_bytes(b"test image")
    target = _ConversationKeyedRecordingTarget()
    formatter = MagicMock(spec=MessageStringNormalizer)

    async def format_messages(messages: list[Message]) -> str:
        return "|".join(message.get_value() for message in messages)

    formatter.normalize_string_async = AsyncMock(side_effect=format_messages)
    node = _make_tap_node(target=target)
    node._request_converters = ConverterConfiguration.from_converters(
        converters=[
            _ImageOutputConverter(output_path=str(image_path)),
            _TextOutputConverter(),
        ]
    )
    node._prepended_conversation_config = PrependedConversationConfig(message_normalizer=formatter)
    node._objective = "objective"

    await node._send_prompt_to_target_async("depth one")
    cloned = node.duplicate()
    cloned._objective = "objective"
    await cloned._send_prompt_to_target_async("depth two")

    assert cloned._prepended_history_send_context
    assert cloned._prepended_history_send_context.is_seed_consumed
    assert target.normalized_requests[-1].get_piece().converted_value == "converted text|response|converted text"


@pytest.fixture
def adversarial_config() -> AttackAdversarialConfig:
    target = MagicMock(spec=PromptTarget)
    target.configuration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_system_prompt=True,
            supports_editable_history=True,
        )
    )
    return AttackAdversarialConfig(target=target)


@pytest.fixture
def scoring_config() -> AttackScoringConfig:
    scorer = MagicMock(spec=TrueFalseScorer)
    return AttackScoringConfig(objective_scorer=scorer)


@pytest.mark.usefixtures("patch_central_database")
def test_crescendo_requires_native_editable_history(
    adversarial_config: AttackAdversarialConfig,
    scoring_config: AttackScoringConfig,
):
    from pyrit.executor.attack.multi_turn.crescendo import CrescendoAttack

    target = _RecordingTarget(supports_multi_turn=True, supports_editable_history=False)
    with pytest.raises(ValueError, match=CapabilityName.EDITABLE_HISTORY.value):
        CrescendoAttack(
            objective_target=target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )


@pytest.mark.usefixtures("patch_central_database")
def test_multi_prompt_sending_requires_native_multi_turn():
    from pyrit.executor.attack.multi_turn.multi_prompt_sending import MultiPromptSendingAttack

    with pytest.raises(ValueError, match=CapabilityName.MULTI_TURN.value):
        MultiPromptSendingAttack(objective_target=_RecordingTarget())


@pytest.mark.usefixtures("patch_central_database")
def test_chunked_request_requires_native_multi_turn():
    from pyrit.executor.attack.multi_turn.chunked_request import ChunkedRequestAttack

    with pytest.raises(ValueError, match=CapabilityName.MULTI_TURN.value):
        ChunkedRequestAttack(objective_target=_RecordingTarget())
