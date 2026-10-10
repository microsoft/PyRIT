# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import uuid
from collections.abc import MutableSequence
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from unit.mocks import get_mock_target_identifier, get_sample_conversations

from pyrit.models import Message, MessagePiece, flatten_to_message_pieces
from pyrit.prompt_target import PromptTarget
from pyrit.score import PromptShieldScorer


@pytest.fixture
def sample_conversations() -> MutableSequence[MessagePiece]:
    conversations = get_sample_conversations()
    return flatten_to_message_pieces(conversations)


@pytest.fixture
def sample_response_json_str() -> str:
    return '{"userPromptAnalysis":{"attackDetected":false},"documentsAnalysis":[{"attackDetected":true}]}'


@pytest.fixture
def promptshield_scorer() -> PromptShieldScorer:
    return PromptShieldScorer(prompt_shield_target=Mock())


def generate_shield_response(response_text: str) -> Message:
    return Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=response_text,
                original_value_data_type="text",
                converted_value=response_text,
                converted_value_data_type="text",
                conversation_id=str(uuid.uuid4()),
            )
        ]
    )


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


def test_prompt_shield_scorer_parsing_without_documents_analysis(promptshield_scorer: PromptShieldScorer):
    response_json_str = '{"userPromptAnalysis":{"attackDetected":false}}'
    result = promptshield_scorer._parse_response_to_boolean_list(response_json_str)
    assert result == [False, False]


async def test_prompt_shield_scorer_metadata_is_the_response_text(sqlite_instance, sample_response_json_str: str):
    """The Score model only accepts flat str/int/float metadata values, so the endpoint
    body has to be stored as the JSON text it arrived as rather than as a parsed object."""

    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = get_mock_target_identifier("MockShieldTarget")
    target.send_prompt_async = AsyncMock(return_value=[generate_shield_response(sample_response_json_str)])

    scorer = PromptShieldScorer(prompt_shield_target=target)
    scores = await scorer.score_text_async(sample_response_json_str)

    assert len(scores) == 1
    # the sample body flags the document, not the user prompt
    assert scores[0].get_value() is True
    assert scores[0].score_metadata == {"raw": sample_response_json_str}
    assert json.loads(scores[0].score_metadata["raw"]) == json.loads(sample_response_json_str)

    persisted_scores = await sqlite_instance.get_scores_async(score_ids=[str(scores[0].id)])
    assert len(persisted_scores) == 1
    assert persisted_scores[0].score_metadata == {"raw": sample_response_json_str}
