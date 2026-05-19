# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from unit.mocks import MockPromptTarget

from pyrit.models import Message
from pyrit.prompt_target import RoundRobinTarget, TargetCapabilities, TargetConfiguration

# ── Construction validation ──────────────────────────────────────────────────


@pytest.mark.usefixtures("patch_central_database")
def test_init_rejects_fewer_than_two_targets():
    with pytest.raises(ValueError, match="at least 2 targets"):
        RoundRobinTarget(targets=[MockPromptTarget()])


@pytest.mark.usefixtures("patch_central_database")
def test_init_rejects_empty_targets():
    with pytest.raises(ValueError, match="at least 2 targets"):
        RoundRobinTarget(targets=[])


@pytest.mark.usefixtures("patch_central_database")
def test_init_rejects_mixed_classes():
    from pyrit.prompt_target import TextTarget

    with pytest.raises(ValueError, match="same concrete class"):
        RoundRobinTarget(targets=[MockPromptTarget(), TextTarget()])


@pytest.mark.usefixtures("patch_central_database")
def test_init_rejects_mismatched_weights_length():
    t1, t2 = MockPromptTarget(), MockPromptTarget()
    with pytest.raises(ValueError, match="weights length"):
        RoundRobinTarget(targets=[t1, t2], weights=[1, 2, 3])


@pytest.mark.usefixtures("patch_central_database")
def test_init_rejects_zero_weight():
    t1, t2 = MockPromptTarget(), MockPromptTarget()
    with pytest.raises(ValueError, match="positive integers"):
        RoundRobinTarget(targets=[t1, t2], weights=[1, 0])


@pytest.mark.usefixtures("patch_central_database")
def test_init_rejects_negative_weight():
    t1, t2 = MockPromptTarget(), MockPromptTarget()
    with pytest.raises(ValueError, match="positive integers"):
        RoundRobinTarget(targets=[t1, t2], weights=[1, -1])


@pytest.mark.usefixtures("patch_central_database")
def test_init_succeeds_with_valid_same_class_targets():
    t1, t2 = MockPromptTarget(), MockPromptTarget()
    rr = RoundRobinTarget(targets=[t1, t2])
    assert rr._targets == [t1, t2]
    assert rr._weights == [1, 1]


@pytest.mark.usefixtures("patch_central_database")
def test_init_succeeds_with_weights():
    t1, t2, t3 = MockPromptTarget(), MockPromptTarget(), MockPromptTarget()
    rr = RoundRobinTarget(targets=[t1, t2, t3], weights=[2, 1, 1])
    assert rr._weights == [2, 1, 1]
    assert rr._rotation == [0, 0, 1, 2]


# ── Capability validation ────────────────────────────────────────────────────


@pytest.mark.usefixtures("patch_central_database")
def test_capabilities_are_intersection_of_inner_targets():
    t1 = MockPromptTarget()  # multi_turn=True, multi_message_pieces=True, etc.
    t2 = MockPromptTarget()

    # Override t2 to have fewer capabilities (but keep multi_turn + editable_history)
    t2._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_multi_message_pieces=False,
            supports_system_prompt=False,
            supports_editable_history=True,
        )
    )

    rr = RoundRobinTarget(targets=[t1, t2])
    caps = rr.capabilities

    # AND of booleans
    assert caps.supports_multi_turn is True
    assert caps.supports_multi_message_pieces is False
    assert caps.supports_system_prompt is False
    assert caps.supports_editable_history is True
    assert caps.supports_json_output is False
    assert caps.supports_json_schema is False


@pytest.mark.usefixtures("patch_central_database")
def test_capabilities_modality_intersection():
    text_only = frozenset({frozenset({"text"})})
    text_and_image = frozenset({frozenset({"text"}), frozenset({"image_path"})})

    t1 = MockPromptTarget()
    t1._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_editable_history=True,
            input_modalities=text_and_image,
            output_modalities=text_only,
        )
    )
    t2 = MockPromptTarget()
    t2._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_editable_history=True,
            input_modalities=text_only,
            output_modalities=text_only,
        )
    )

    rr = RoundRobinTarget(targets=[t1, t2])
    assert rr.capabilities.input_modalities == text_only
    assert rr.capabilities.output_modalities == text_only


@pytest.mark.usefixtures("patch_central_database")
def test_capabilities_empty_modality_intersection_raises():
    text_only = frozenset({frozenset({"text"})})
    image_only = frozenset({frozenset({"image_path"})})

    t1 = MockPromptTarget()
    t1._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_editable_history=True,
            input_modalities=text_only,
        )
    )
    t2 = MockPromptTarget()
    t2._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_editable_history=True,
            input_modalities=image_only,
        )
    )

    with pytest.raises(ValueError, match="input modalities"):
        RoundRobinTarget(targets=[t1, t2])


@pytest.mark.usefixtures("patch_central_database")
def test_init_rejects_targets_without_multi_turn():
    t1 = MockPromptTarget()
    t1._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(supports_multi_turn=False, supports_editable_history=True)
    )
    t2 = MockPromptTarget()
    t2._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(supports_multi_turn=False, supports_editable_history=True)
    )
    with pytest.raises(ValueError, match="required capability"):
        RoundRobinTarget(targets=[t1, t2])


@pytest.mark.usefixtures("patch_central_database")
def test_init_rejects_targets_without_editable_history():
    t1 = MockPromptTarget()
    t1._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(supports_multi_turn=True, supports_editable_history=False)
    )
    t2 = MockPromptTarget()
    t2._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(supports_multi_turn=True, supports_editable_history=False)
    )
    with pytest.raises(ValueError, match="required capability"):
        RoundRobinTarget(targets=[t1, t2])


# ── Round-robin selection ────────────────────────────────────────────────────


@pytest.mark.usefixtures("patch_central_database")
def test_next_target_round_robins():
    t1, t2 = MockPromptTarget(), MockPromptTarget()
    rr = RoundRobinTarget(targets=[t1, t2])

    assert rr._next_target() is t1
    assert rr._next_target() is t2
    assert rr._next_target() is t1
    assert rr._next_target() is t2


@pytest.mark.usefixtures("patch_central_database")
def test_next_target_weighted_rotation():
    t1, t2 = MockPromptTarget(), MockPromptTarget()
    rr = RoundRobinTarget(targets=[t1, t2], weights=[2, 1])

    assert rr._next_target() is t1
    assert rr._next_target() is t1
    assert rr._next_target() is t2
    # Wraps around
    assert rr._next_target() is t1


# ── Delegation & metadata ───────────────────────────────────────────────────


@pytest.mark.usefixtures("patch_central_database")
async def test_send_prompt_to_target_delegates_to_inner_target():
    t1, t2 = MockPromptTarget(), MockPromptTarget()
    rr = RoundRobinTarget(targets=[t1, t2])

    message = Message.from_prompt(prompt="test prompt", role="user")
    message.message_pieces[0].conversation_id = "delegate-test"

    response = await rr._send_prompt_to_target_async(normalized_conversation=[message])

    assert t1.prompt_sent == ["test prompt"]
    assert t2.prompt_sent == []
    assert len(response) == 1


@pytest.mark.usefixtures("patch_central_database")
async def test_send_prompt_to_target_records_inner_target_in_metadata():
    t1, t2 = MockPromptTarget(), MockPromptTarget()
    rr = RoundRobinTarget(targets=[t1, t2])

    message = Message.from_prompt(prompt="metadata test", role="user")
    message.message_pieces[0].conversation_id = "meta-test"

    responses = await rr._send_prompt_to_target_async(normalized_conversation=[message])

    # The response should have inner_target_identifier in metadata
    response_piece = responses[0].message_pieces[0]
    assert response_piece.prompt_metadata["inner_target_identifier"] == t1.get_identifier().hash


@pytest.mark.usefixtures("patch_central_database")
async def test_send_prompt_to_target_round_robins_across_calls():
    t1, t2 = MockPromptTarget(), MockPromptTarget()
    rr = RoundRobinTarget(targets=[t1, t2])

    msg1 = Message.from_prompt(prompt="first", role="user")
    msg1.message_pieces[0].conversation_id = "rr-1"
    msg2 = Message.from_prompt(prompt="second", role="user")
    msg2.message_pieces[0].conversation_id = "rr-2"

    await rr._send_prompt_to_target_async(normalized_conversation=[msg1])
    await rr._send_prompt_to_target_async(normalized_conversation=[msg2])

    assert t1.prompt_sent == ["first"]
    assert t2.prompt_sent == ["second"]


# ── Identifier ───────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("patch_central_database")
def test_identifier_includes_children_and_weights():
    t1, t2 = MockPromptTarget(), MockPromptTarget()
    rr = RoundRobinTarget(targets=[t1, t2], weights=[3, 1])

    identifier = rr.get_identifier()
    assert identifier.class_name == "RoundRobinTarget"
    assert identifier.children is not None
    assert "targets" in identifier.children
    assert len(identifier.children["targets"]) == 2
    assert identifier.params["weights"] == [3, 1]


# ── End-to-end with send_prompt_async ────────────────────────────────────────


@pytest.mark.usefixtures("patch_central_database")
async def test_full_send_prompt_async_keeps_round_robin_identifier():
    """
    The full flow: PromptNormalizer stamps the round-robin identifier,
    send_prompt_async runs, and entries keep the round-robin identifier.
    Inner target info is in response metadata.
    """
    t1, t2 = MockPromptTarget(), MockPromptTarget()
    rr = RoundRobinTarget(targets=[t1, t2])

    message = Message.from_prompt(prompt="end to end test", role="user")
    conv_id = "e2e-conv"
    for piece in message.message_pieces:
        piece.conversation_id = conv_id
        # Simulate what PromptNormalizer does
        piece.prompt_target_identifier = rr.get_identifier()

    responses = await rr.send_prompt_async(message=message)

    # The request should still have the round-robin's identifier
    assert message.message_pieces[0].prompt_target_identifier == rr.get_identifier()

    # Only t1 should have received the prompt (first in rotation)
    assert t1.prompt_sent == ["end to end test"]
    assert t2.prompt_sent == []


# ── Behavioral param validation ──────────────────────────────────────────────


@pytest.mark.usefixtures("patch_central_database")
def test_init_rejects_mismatched_underlying_model():
    """MockPromptTarget has no underlying_model by default, so we use
    targets with explicit identifier params to test validation."""
    from pyrit.identifiers import ComponentIdentifier
    from pyrit.prompt_target.round_robin_target import _validate_behavioral_consistency

    t1 = MockPromptTarget()
    t2 = MockPromptTarget()

    # Override identifiers with different underlying_model_name
    t1._identifier = ComponentIdentifier(
        class_name="MockPromptTarget",
        class_module="unit.mocks",
        params={"underlying_model_name": "gpt-4o", "temperature": 0.7, "top_p": 1.0},
    )
    t2._identifier = ComponentIdentifier(
        class_name="MockPromptTarget",
        class_module="unit.mocks",
        params={"underlying_model_name": "gpt-3.5-turbo", "temperature": 0.7, "top_p": 1.0},
    )

    with pytest.raises(ValueError, match="underlying_model_name"):
        _validate_behavioral_consistency([t1, t2])


@pytest.mark.usefixtures("patch_central_database")
def test_init_rejects_mismatched_temperature():
    from pyrit.identifiers import ComponentIdentifier
    from pyrit.prompt_target.round_robin_target import _validate_behavioral_consistency

    t1 = MockPromptTarget()
    t2 = MockPromptTarget()

    t1._identifier = ComponentIdentifier(
        class_name="MockPromptTarget",
        class_module="unit.mocks",
        params={"underlying_model_name": "gpt-4o", "temperature": 0.0, "top_p": 1.0},
    )
    t2._identifier = ComponentIdentifier(
        class_name="MockPromptTarget",
        class_module="unit.mocks",
        params={"underlying_model_name": "gpt-4o", "temperature": 1.0, "top_p": 1.0},
    )

    with pytest.raises(ValueError, match="temperature"):
        _validate_behavioral_consistency([t1, t2])


@pytest.mark.usefixtures("patch_central_database")
def test_init_accepts_matching_behavioral_params():
    from pyrit.identifiers import ComponentIdentifier
    from pyrit.prompt_target.round_robin_target import _validate_behavioral_consistency

    t1 = MockPromptTarget()
    t2 = MockPromptTarget()

    t1._identifier = ComponentIdentifier(
        class_name="MockPromptTarget",
        class_module="unit.mocks",
        params={
            "underlying_model_name": "gpt-4o",
            "temperature": 0.7,
            "top_p": 1.0,
            "endpoint": "https://east.openai.azure.com",
        },
    )
    t2._identifier = ComponentIdentifier(
        class_name="MockPromptTarget",
        class_module="unit.mocks",
        params={
            "underlying_model_name": "gpt-4o",
            "temperature": 0.7,
            "top_p": 1.0,
            "endpoint": "https://west.openai.azure.com",
        },
    )

    # Should not raise — behavioral params match, endpoints differ (that's fine)
    _validate_behavioral_consistency([t1, t2])


@pytest.mark.usefixtures("patch_central_database")
def test_init_uses_model_name_fallback_for_underlying_model():
    from pyrit.identifiers import ComponentIdentifier
    from pyrit.prompt_target.round_robin_target import _validate_behavioral_consistency

    t1 = MockPromptTarget()
    t2 = MockPromptTarget()

    # t1 has underlying_model_name, t2 only has model_name (fallback)
    t1._identifier = ComponentIdentifier(
        class_name="MockPromptTarget",
        class_module="unit.mocks",
        params={"underlying_model_name": "gpt-4o", "model_name": "gpt4o-deployment"},
    )
    t2._identifier = ComponentIdentifier(
        class_name="MockPromptTarget",
        class_module="unit.mocks",
        params={"underlying_model_name": "", "model_name": "gpt-4o"},
    )

    # Both resolve to "gpt-4o" — should not raise
    _validate_behavioral_consistency([t1, t2])
