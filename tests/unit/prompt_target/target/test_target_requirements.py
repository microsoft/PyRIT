# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.prompt_target import (
    CHAT_CONSUMER_REQUIREMENTS,
    CapabilityName,
    TargetRequirements,
)


def test_default_requirements_require_nothing():
    assert TargetRequirements().required == frozenset()


def test_construction_from_frozenset():
    reqs = TargetRequirements(
        required=frozenset({CapabilityName.MULTI_TURN, CapabilityName.JSON_OUTPUT}),
    )
    assert reqs.required == {CapabilityName.MULTI_TURN, CapabilityName.JSON_OUTPUT}


def test_chat_consumer_requirements_shape():
    assert CHAT_CONSUMER_REQUIREMENTS.required == {
        CapabilityName.SYSTEM_PROMPT,
        CapabilityName.MULTI_TURN,
    }


def test_requirements_are_frozen():
    reqs = TargetRequirements(required=frozenset({CapabilityName.MULTI_TURN}))
    with pytest.raises(Exception):
        reqs.required = frozenset()  # type: ignore[misc]
