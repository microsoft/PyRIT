# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock

import pytest

from pyrit.prompt_target import (
    CHAT_CONSUMER_REQUIREMENTS,
    CapabilityName,
    TargetRequirements,
)
from pyrit.prompt_target.common.target_capabilities import (
    CapabilityHandlingPolicy,
    TargetCapabilities,
    UnsupportedCapabilityBehavior,
)
from pyrit.prompt_target.common.target_configuration import TargetConfiguration


def _make_target(*, configuration: TargetConfiguration) -> MagicMock:
    target = MagicMock()
    target.configuration = configuration
    return target


def test_default_requirements_require_nothing():
    assert TargetRequirements().required == frozenset()


def test_construction_from_frozenset():
    reqs = TargetRequirements(
        required=frozenset({CapabilityName.MULTI_TURN, CapabilityName.JSON_OUTPUT}),
    )
    assert reqs.required == {CapabilityName.MULTI_TURN, CapabilityName.JSON_OUTPUT}


def test_chat_consumer_requirements_shape():
    assert CHAT_CONSUMER_REQUIREMENTS.required == {
        CapabilityName.EDITABLE_HISTORY,
        CapabilityName.MULTI_TURN,
    }


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

    CHAT_CONSUMER_REQUIREMENTS.validate(target=target)


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
