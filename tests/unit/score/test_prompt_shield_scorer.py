# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import MutableSequence
from unittest.mock import Mock

import pytest
from unit.mocks import get_sample_conversations

from pyrit.models import Message, MessagePiece
from pyrit.score import PromptShieldScorer


@pytest.fixture
def sample_conversations() -> MutableSequence[MessagePiece]:
    conversations = get_sample_conversations()
    return Message.flatten_to_message_pieces(conversations)


@pytest.fixture
def sample_response_json_str() -> str:
    return '{"userPromptAnalysis":{"attackDetected":false},"documentsAnalysis":[{"attackDetected":true}]}'


@pytest.fixture
def promptshield_scorer() -> PromptShieldScorer:
    return PromptShieldScorer(prompt_shield_target=Mock())


@pytest.fixture
def sample_delineated_prompt_as_str() -> str:
    sample: str = """
    Mock userPrompt
    <document>
    mock document
    </document>
    """
    return sample


def test_prompt_shield_scorer_parsing(promptshield_scorer: PromptShieldScorer, sample_response_json_str: str):
    assert any(promptshield_scorer._parse_response_to_boolean_list(sample_response_json_str))
