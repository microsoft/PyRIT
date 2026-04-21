# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_target.common.target_capabilities import (
    CapabilityHandlingPolicy,
    CapabilityName,
    TargetCapabilities,
    UnsupportedCapabilityBehavior,
)
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.target_requirements import TargetRequirements


@pytest.fixture
def adapt_all_policy():
    return CapabilityHandlingPolicy(
        behaviors={
            CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT,
            CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.ADAPT,
            CapabilityName.JSON_SCHEMA: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.JSON_OUTPUT: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.MULTI_MESSAGE_PIECES: UnsupportedCapabilityBehavior.RAISE,
            CapabilityName.EDITABLE_HISTORY: UnsupportedCapabilityBehavior.RAISE,
        }
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_init_default_has_empty_capabilities():
    reqs = TargetRequirements()
    assert reqs.required_capabilities == frozenset()


def test_init_with_capabilities():
    reqs = TargetRequirements(
        required_capabilities=frozenset({CapabilityName.MULTI_TURN, CapabilityName.SYSTEM_PROMPT})
    )
    assert CapabilityName.MULTI_TURN in reqs.required_capabilities
    assert CapabilityName.SYSTEM_PROMPT in reqs.required_capabilities


# ---------------------------------------------------------------------------
# validate — all pass
# ---------------------------------------------------------------------------


def test_validate_passes_when_target_supports_all_natively():
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True)
    config = TargetConfiguration(capabilities=caps)
    reqs = TargetRequirements(
        required_capabilities=frozenset({CapabilityName.MULTI_TURN, CapabilityName.SYSTEM_PROMPT})
    )
    reqs.validate(configuration=config)


def test_validate_passes_when_policy_is_adapt(adapt_all_policy):
    caps = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=False)
    config = TargetConfiguration(capabilities=caps, policy=adapt_all_policy)
    reqs = TargetRequirements(
        required_capabilities=frozenset({CapabilityName.MULTI_TURN, CapabilityName.SYSTEM_PROMPT})
    )
    reqs.validate(configuration=config)


def test_validate_passes_with_empty_requirements():
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True)
    config = TargetConfiguration(capabilities=caps)
    reqs = TargetRequirements()
    reqs.validate(configuration=config)


# ---------------------------------------------------------------------------
# validate — failures
# ---------------------------------------------------------------------------


def test_validate_raises_when_capability_missing_and_no_policy():
    # EDITABLE_HISTORY has no normalizer and no handling policy — validate raises.
    caps = TargetCapabilities(supports_editable_history=False, supports_multi_turn=True, supports_system_prompt=True)
    config = TargetConfiguration(capabilities=caps)
    reqs = TargetRequirements(required_capabilities=frozenset({CapabilityName.EDITABLE_HISTORY}))
    with pytest.raises(ValueError, match="supports_editable_history"):
        reqs.validate(configuration=config)


def test_validate_raises_when_capability_missing_and_policy_raise(adapt_all_policy):
    # json_output is missing and the policy is RAISE — validate raises.
    caps = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=False, supports_json_output=False)
    config = TargetConfiguration(capabilities=caps, policy=adapt_all_policy)
    reqs = TargetRequirements(required_capabilities=frozenset({CapabilityName.JSON_OUTPUT}))
    with pytest.raises(ValueError, match="supports_json_output"):
        reqs.validate(configuration=config)


def test_validate_collects_all_unsatisfied_capabilities(adapt_all_policy):
    """When multiple capabilities are missing, validate reports all violations."""
    caps = TargetCapabilities(
        supports_multi_turn=False,
        supports_system_prompt=False,
        supports_json_output=False,
        supports_editable_history=False,
    )
    config = TargetConfiguration(capabilities=caps, policy=adapt_all_policy)
    # json_output => RAISE, editable_history => no policy (raises)
    reqs = TargetRequirements(
        required_capabilities=frozenset({CapabilityName.JSON_OUTPUT, CapabilityName.EDITABLE_HISTORY})
    )
    with pytest.raises(ValueError, match="2 required capability") as exc_info:
        reqs.validate(configuration=config)
    assert "supports_json_output" in str(exc_info.value)
    assert "supports_editable_history" in str(exc_info.value)


def test_validate_mixed_adapt_and_raise(adapt_all_policy):
    """One capability adapts but another raises — validate should raise."""
    caps = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=False, supports_json_output=False)
    config = TargetConfiguration(capabilities=caps, policy=adapt_all_policy)
    # multi_turn and system_prompt => ADAPT (OK), json_output => RAISE (fail)
    reqs = TargetRequirements(
        required_capabilities=frozenset(
            {CapabilityName.MULTI_TURN, CapabilityName.SYSTEM_PROMPT, CapabilityName.JSON_OUTPUT}
        )
    )
    with pytest.raises(ValueError, match="supports_json_output"):
        reqs.validate(configuration=config)


# ---------------------------------------------------------------------------
# required_native_capabilities
# ---------------------------------------------------------------------------


def test_validate_native_passes_when_supported_natively():
    caps = TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True)
    config = TargetConfiguration(capabilities=caps)
    reqs = TargetRequirements(required_native_capabilities=frozenset({CapabilityName.MULTI_TURN}))
    reqs.validate(configuration=config)


def test_validate_native_raises_when_only_adapted(adapt_all_policy):
    # multi_turn is missing but ADAPT — acceptable for required_capabilities
    # but not for required_native_capabilities.
    caps = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=True)
    config = TargetConfiguration(capabilities=caps, policy=adapt_all_policy)
    reqs = TargetRequirements(required_native_capabilities=frozenset({CapabilityName.MULTI_TURN}))
    with pytest.raises(ValueError, match="natively support 'supports_multi_turn'"):
        reqs.validate(configuration=config)


def test_validate_native_and_adapted_mixed(adapt_all_policy):
    # system_prompt adapted (OK for required_capabilities); multi_turn required
    # natively (FAIL — only adapted).
    caps = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=False)
    config = TargetConfiguration(capabilities=caps, policy=adapt_all_policy)
    reqs = TargetRequirements(
        required_capabilities=frozenset({CapabilityName.SYSTEM_PROMPT}),
        required_native_capabilities=frozenset({CapabilityName.MULTI_TURN}),
    )
    with pytest.raises(ValueError, match="natively support 'supports_multi_turn'"):
        reqs.validate(configuration=config)


def test_validate_native_collects_multiple_violations():
    caps = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=False)
    config = TargetConfiguration(capabilities=caps)
    reqs = TargetRequirements(
        required_native_capabilities=frozenset({CapabilityName.MULTI_TURN, CapabilityName.SYSTEM_PROMPT})
    )
    with pytest.raises(ValueError, match="2 required capability") as exc_info:
        reqs.validate(configuration=config)
    assert "supports_multi_turn" in str(exc_info.value)
    assert "supports_system_prompt" in str(exc_info.value)
