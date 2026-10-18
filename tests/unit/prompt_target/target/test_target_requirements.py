# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock

import pytest

from pyrit.prompt_target import (
    CHAT_TARGET_REQUIREMENTS,
    CapabilityName,
    PromptTarget,
    TargetRequirements,
)
from pyrit.prompt_target.common.target_capabilities import (
    CapabilityHandlingPolicy,
    TargetCapabilities,
    UnsupportedCapabilityBehavior,
)
from pyrit.prompt_target.common.target_configuration import TargetConfiguration


def _make_target(*, configuration: TargetConfiguration) -> MagicMock:
    target = MagicMock(spec=PromptTarget)
    target.configuration = configuration
    target.capabilities = configuration.capabilities
    return target


def test_default_requirements_require_nothing():
    assert TargetRequirements().required == frozenset()


def test_check_retains_all_validation_reasons() -> None:
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(supports_multi_turn=False, supports_editable_history=False)
        )
    )
    requirements = TargetRequirements(
        native_required=frozenset({CapabilityName.MULTI_TURN}),
        required=frozenset({CapabilityName.EDITABLE_HISTORY}),
        required_input_modalities=frozenset({frozenset({"image_path"})}),
        required_output_modalities=frozenset({frozenset({"audio_path"})}),
    )

    with pytest.raises(ValueError) as raised:
        requirements.validate(target=target)

    assert requirements.check(target=target) == [str(raised.value)]
    assert "4 required capability" in str(raised.value)


def test_check_accepts_real_adaptation_but_not_policy_without_adapter() -> None:
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(supports_multi_turn=False, supports_editable_history=False),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT,
                    CapabilityName.EDITABLE_HISTORY: UnsupportedCapabilityBehavior.ADAPT,
                }
            ),
        )
    )
    capabilities = target.configuration.capabilities
    policy = target.configuration.policy

    assert TargetRequirements(required=frozenset({CapabilityName.MULTI_TURN})).check(target=target) == []
    assert TargetRequirements(required=frozenset({CapabilityName.EDITABLE_HISTORY})).check(target=target)
    assert target.configuration.capabilities is capabilities
    assert target.configuration.policy is policy


@pytest.mark.parametrize(
    "editable,system,compatible", [(True, False, True), (False, True, True), (False, False, False)]
)
def test_check_preserves_self_contained_judge_rules(*, editable: bool, system: bool, compatible: bool) -> None:
    from pyrit.score.scorer import _SelfContainedJudgeTargetRequirements

    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_editable_history=editable, supports_system_prompt=system, supports_multi_turn=True
            )
        )
    )
    requirements = _SelfContainedJudgeTargetRequirements()

    if compatible:
        requirements.validate(target=target)
        assert requirements.check(target=target) == []
    else:
        with pytest.raises(ValueError) as raised:
            requirements.validate(target=target)
        assert requirements.check(target=target) == [str(raised.value)]
        assert "system_prompt" in str(raised.value)


def test_check_propagates_unexpected_validation_errors() -> None:
    class BrokenRequirements(TargetRequirements):
        def validate(self, *, target: PromptTarget) -> None:
            raise RuntimeError("unexpected failure")

    with pytest.raises(RuntimeError, match="unexpected failure"):
        BrokenRequirements().check(
            target=_make_target(configuration=TargetConfiguration(capabilities=TargetCapabilities()))
        )


def test_construction_from_frozenset():
    reqs = TargetRequirements(
        required=frozenset({CapabilityName.MULTI_TURN, CapabilityName.JSON_OUTPUT}),
    )
    assert reqs.required == {CapabilityName.MULTI_TURN, CapabilityName.JSON_OUTPUT}


def test_chat_target_requirements_shape():
    assert CHAT_TARGET_REQUIREMENTS.required == {
        CapabilityName.EDITABLE_HISTORY,
        CapabilityName.MULTI_TURN,
    }
    assert CHAT_TARGET_REQUIREMENTS.native_required == set()


def test_requirements_are_frozen():
    reqs = TargetRequirements(required=frozenset({CapabilityName.MULTI_TURN}))
    with pytest.raises(Exception):
        reqs.required = frozenset()  # type: ignore[misc]


def test_validate_passes_on_native_support():
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=True,
                supports_editable_history=True,
            ),
        ),
    )

    CHAT_TARGET_REQUIREMENTS.validate(target=target)


def test_validate_passes_when_policy_is_adapt():
    # Note: EDITABLE_HISTORY is not adaptable, so this test uses a custom
    # requirement over capabilities that the policy can adapt.
    reqs = TargetRequirements(required=frozenset({CapabilityName.MULTI_TURN, CapabilityName.SYSTEM_PROMPT}))
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=False,
                supports_system_prompt=False,
            ),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT,
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT,
                },
            ),
        ),
    )

    reqs.validate(target=target)


def test_validate_raises_when_capability_neither_native_nor_adapt():
    reqs = TargetRequirements(required=frozenset({CapabilityName.MULTI_TURN, CapabilityName.SYSTEM_PROMPT}))
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=True,
                supports_system_prompt=False,
            ),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
                },
            ),
        ),
    )

    with pytest.raises(ValueError, match=CapabilityName.SYSTEM_PROMPT.value):
        reqs.validate(target=target)


def test_validate_empty_required_always_passes():
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
                },
            ),
        ),
    )

    TargetRequirements().validate(target=target)


# ---------------------------------------------------------------------------
# native_required branch
# ---------------------------------------------------------------------------


def test_native_required_passes_when_capability_is_native():
    reqs = TargetRequirements(native_required=frozenset({CapabilityName.MULTI_TURN}))
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(supports_multi_turn=True),
        ),
    )

    reqs.validate(target=target)


def test_native_required_raises_when_capability_only_satisfied_by_adapt_policy():
    # MULTI_TURN is satisfied by an ADAPT policy entry, which would let it
    # pass the ``required`` tier — but ``native_required`` must reject
    # adaptation outright.
    reqs = TargetRequirements(native_required=frozenset({CapabilityName.MULTI_TURN}))
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(supports_multi_turn=False),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT,
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
                },
            ),
        ),
    )

    with pytest.raises(ValueError, match="natively"):
        reqs.validate(target=target)


def test_native_required_raises_when_capability_missing():
    reqs = TargetRequirements(native_required=frozenset({CapabilityName.SYSTEM_PROMPT}))
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(supports_system_prompt=False),
        ),
    )

    with pytest.raises(ValueError, match=CapabilityName.SYSTEM_PROMPT.value):
        reqs.validate(target=target)


def test_native_required_message_mentions_failing_capability():
    reqs = TargetRequirements(native_required=frozenset({CapabilityName.MULTI_TURN}))
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(supports_multi_turn=False),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT,
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
                },
            ),
        ),
    )

    with pytest.raises(ValueError, match=CapabilityName.MULTI_TURN.value):
        reqs.validate(target=target)


def test_native_required_takes_precedence_when_capability_is_in_both_tiers():
    # When a capability appears in both ``required`` and ``native_required``,
    # an ADAPT policy is not enough — the native_required tier must reject it.
    reqs = TargetRequirements(
        required=frozenset({CapabilityName.MULTI_TURN}),
        native_required=frozenset({CapabilityName.MULTI_TURN}),
    )
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(supports_multi_turn=False),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT,
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
                },
            ),
        ),
    )

    with pytest.raises(ValueError, match="natively"):
        reqs.validate(target=target)


def test_native_required_and_required_both_pass_when_natively_supported():
    reqs = TargetRequirements(
        required=frozenset({CapabilityName.MULTI_TURN}),
        native_required=frozenset({CapabilityName.SYSTEM_PROMPT}),
    )
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=True,
                supports_system_prompt=True,
            ),
        ),
    )

    reqs.validate(target=target)


def test_default_native_required_is_empty():
    assert TargetRequirements().native_required == frozenset()


def test_validate_aggregates_all_violations():
    # Two separate violations: one ``native_required`` only satisfied via ADAPT,
    # and one ``required`` whose policy is RAISE. Both should appear in the
    # raised error so callers don't have to fix-and-rerun one at a time.
    reqs = TargetRequirements(
        required=frozenset({CapabilityName.JSON_OUTPUT}),
        native_required=frozenset({CapabilityName.MULTI_TURN}),
    )
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=False,
                supports_json_output=False,
            ),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT,
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.RAISE,
                    CapabilityName.JSON_OUTPUT: UnsupportedCapabilityBehavior.RAISE,
                },
            ),
        ),
    )

    with pytest.raises(ValueError) as exc_info:
        reqs.validate(target=target)

    message = str(exc_info.value)
    assert "2 required capability" in message
    assert CapabilityName.MULTI_TURN.value in message
    assert CapabilityName.JSON_OUTPUT.value in message


# ---------------------------------------------------------------------------
# Modality validation
# ---------------------------------------------------------------------------


def test_validate_passes_when_no_modality_requirements():
    """Backward compat: empty modality requirements should always pass."""
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(),
        ),
    )
    TargetRequirements().validate(target=target)


def test_validate_passes_when_input_modality_matches():
    reqs = TargetRequirements(
        required_input_modalities=frozenset({frozenset({"text"})}),
    )
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                input_modalities=frozenset({frozenset({"text"})}),
            ),
        ),
    )
    reqs.validate(target=target)


def test_validate_passes_when_target_modality_is_superset():
    reqs = TargetRequirements(
        required_input_modalities=frozenset({frozenset({"text"})}),
    )
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                input_modalities=frozenset({frozenset({"text", "image_path"})}),
            ),
        ),
    )
    reqs.validate(target=target)


def test_validate_fails_on_missing_input_modality():
    reqs = TargetRequirements(
        required_input_modalities=frozenset({frozenset({"image_path"})}),
    )
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                input_modalities=frozenset({frozenset({"text"})}),
            ),
        ),
    )
    with pytest.raises(ValueError, match="input modality"):
        reqs.validate(target=target)


def test_validate_fails_on_missing_output_modality():
    reqs = TargetRequirements(
        required_output_modalities=frozenset({frozenset({"audio_path"})}),
    )
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                output_modalities=frozenset({frozenset({"text"})}),
            ),
        ),
    )
    with pytest.raises(ValueError, match="output modality"):
        reqs.validate(target=target)


def test_validate_aggregates_modality_and_capability_errors():
    reqs = TargetRequirements(
        native_required=frozenset({CapabilityName.MULTI_TURN}),
        required_input_modalities=frozenset({frozenset({"image_path"})}),
    )
    target = _make_target(
        configuration=TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=False,
                input_modalities=frozenset({frozenset({"text"})}),
            ),
        ),
    )
    with pytest.raises(ValueError) as exc_info:
        reqs.validate(target=target)

    message = str(exc_info.value)
    assert "2 required capability" in message
    assert CapabilityName.MULTI_TURN.value in message
    assert "input modality" in message


def test_default_modality_requirements_are_empty():
    reqs = TargetRequirements()
    assert reqs.required_input_modalities == frozenset()
    assert reqs.required_output_modalities == frozenset()
