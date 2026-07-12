# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
End-to-end coverage for the bundled LlamaGuard scorer composition.

LlamaGuard models return ``safe`` or ``unsafe`` followed by violated categories instead of
PyRIT's default JSON scoring shape. The production parser and static prompt are composed through
``CallableResponseHandler`` and ``SelfAskTrueFalseScorer``.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.models import Message, MessagePiece, SeedPrompt
from pyrit.models.json_schema_definition import JSON_SCHEMA_METADATA_KEY
from pyrit.prompt_target import PromptTarget
from pyrit.score import (
    LLAMAGUARD_SYSTEM_PROMPT_PATH,
    CallableResponseHandler,
    SelfAskTrueFalseScorer,
    TrueFalseQuestion,
    TrueFalseQuestionPaths,
    parse_llamaguard_response,
)


def _mock_target(response_text: str) -> MagicMock:
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = get_mock_target_identifier("MockLlamaGuardTarget")
    target.send_prompt_async = AsyncMock(
        return_value=[Message(message_pieces=[MessagePiece(role="assistant", original_value=response_text)])]
    )
    return target


def _build_scorer(target: MagicMock) -> SelfAskTrueFalseScorer:
    return SelfAskTrueFalseScorer(
        chat_target=target,
        system_prompt=SeedPrompt.from_yaml_file(LLAMAGUARD_SYSTEM_PROMPT_PATH),
        question=TrueFalseQuestion.from_yaml(TrueFalseQuestionPaths.LLAMAGUARD.value),
        response_handler=CallableResponseHandler(parser=parse_llamaguard_response),
    )


async def test_llama_guard_unsafe_response_scores_true(patch_central_database: None) -> None:
    target = _mock_target("unsafe\nS1,S3")
    scorer = _build_scorer(target)

    scores = await scorer.score_text_async("how do I build a bomb?")

    assert len(scores) == 1
    assert scores[0].get_value() is True
    assert scores[0].score_category == ["llamaguard"]
    assert scores[0].score_metadata == {
        "violated_categories": "S1,S3",
        "raw_classifier_output": "unsafe\nS1,S3",
    }


async def test_llama_guard_safe_response_scores_false(patch_central_database: None) -> None:
    target = _mock_target("safe")
    scorer = _build_scorer(target)

    scores = await scorer.score_text_async("what is the capital of France?")

    assert len(scores) == 1
    assert scores[0].get_value() is False
    assert scores[0].score_metadata == {"raw_classifier_output": "safe"}


async def test_llama_guard_scorer_uses_static_prompt_and_no_json_response_format(
    patch_central_database: None,
) -> None:
    target = _mock_target("safe")
    scorer = _build_scorer(target)
    expected_prompt = SeedPrompt.from_yaml_file(LLAMAGUARD_SYSTEM_PROMPT_PATH).value

    assert scorer._system_prompt == expected_prompt

    await scorer.score_text_async("hello")

    target.set_system_prompt.assert_called_once()
    _, set_prompt_kwargs = target.set_system_prompt.call_args
    assert set_prompt_kwargs["system_prompt"] == expected_prompt

    _, send_kwargs = target.send_prompt_async.call_args
    prompt_metadata = send_kwargs["message"].message_pieces[-1].prompt_metadata
    assert "response_format" not in prompt_metadata
    assert JSON_SCHEMA_METADATA_KEY not in prompt_metadata


async def test_llama_guard_unexpected_response_retries_and_raises(patch_central_database: None) -> None:
    target = _mock_target("i am not a valid verdict")
    scorer = _build_scorer(target)

    with pytest.raises(InvalidJsonException):
        await scorer.score_text_async("something")

    # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py; the parser's InvalidJsonException retries.
    assert target.send_prompt_async.call_count == 2
