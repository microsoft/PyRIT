# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.exceptions.exception_classes import InvalidJsonException
from pyrit.memory.central_memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import Message, MessagePiece
from pyrit.score import SelfAskTrueFalseScorer, TrueFalseQuestionPaths


@pytest.fixture
def scorer_true_false_response() -> Message:
    json_response = (
        dedent(
            """
        {"score_value": "True", "description": "This is true", "rationale": "rationale for true"}
        """
        )
        .strip()
        .replace("\n", " ")
    )

    return Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])


@pytest.mark.asyncio
async def test_true_false_scorer_score(patch_central_database, scorer_true_false_response: Message):
    chat_target = MagicMock()

    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])
    scorer = SelfAskTrueFalseScorer(
        chat_target=chat_target, true_false_question_path=TrueFalseQuestionPaths.GROUNDED.value
    )

    score = await scorer.score_text_async("true false")

    assert len(score) == 1
    assert score[0].get_value() is True
    assert score[0].score_value_description == "This is true"
    assert score[0].score_rationale == "rationale for true"
    assert score[0].scorer_class_identifier.class_name == "SelfAskTrueFalseScorer"


@pytest.mark.asyncio
async def test_true_false_scorer_set_system_prompt(patch_central_database, scorer_true_false_response: Message):
    chat_target = MagicMock()
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])

    scorer = SelfAskTrueFalseScorer(
        chat_target=chat_target, true_false_question_path=TrueFalseQuestionPaths.GROUNDED.value
    )

    await scorer.score_text_async("true false")

    chat_target.set_system_prompt.assert_called_once()

    # assert that the category content was loaded into system prompt
    assert "# Instructions" in scorer._system_prompt
    assert "Semantic Alignment:" in scorer._system_prompt


@pytest.mark.asyncio
async def test_true_false_scorer_adds_to_memory(scorer_true_false_response: Message):
    memory = MagicMock(MemoryInterface)
    chat_target = MagicMock()
    chat_target.send_prompt_async = AsyncMock(return_value=[scorer_true_false_response])
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = SelfAskTrueFalseScorer(
            chat_target=chat_target, true_false_question_path=TrueFalseQuestionPaths.GROUNDED.value
        )

        await scorer.score_text_async(text="string")

        memory.add_scores_to_memory.assert_called_once()


@pytest.mark.asyncio
async def test_self_ask_scorer_bad_json_exception_retries(patch_central_database):
    chat_target = MagicMock()

    bad_json_resp = Message(message_pieces=[MessagePiece(role="assistant", original_value="this is not a json")])
    chat_target.send_prompt_async = AsyncMock(return_value=[bad_json_resp])
    scorer = SelfAskTrueFalseScorer(
        chat_target=chat_target, true_false_question_path=TrueFalseQuestionPaths.GROUNDED.value
    )

    with pytest.raises(InvalidJsonException, match="Error in scorer SelfAskTrueFalseScorer"):
        await scorer.score_text_async("this has no bullying")

    # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
    assert chat_target.send_prompt_async.call_count == 2


@pytest.mark.asyncio
async def test_self_ask_objective_scorer_bad_json_exception_retries(patch_central_database):
    chat_target = MagicMock()

    json_response = (
        dedent(
            """
            {"badly_named_value": "True", "rationale": "rationale for true"}
            """
        )
        .strip()
        .replace("\n", " ")
    )

    bad_json_resp = Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])

    chat_target.send_prompt_async = AsyncMock(return_value=[bad_json_resp])
    scorer = SelfAskTrueFalseScorer(
        chat_target=chat_target, true_false_question_path=TrueFalseQuestionPaths.GROUNDED.value
    )

    with pytest.raises(InvalidJsonException, match="Error in scorer SelfAskTrueFalseScorer"):
        await scorer.score_text_async("this has no bullying")

    # RETRY_MAX_NUM_ATTEMPTS is set to 2 in conftest.py
    assert chat_target.send_prompt_async.call_count == 2


def test_self_ask_true_false_scorer_identifier_has_system_prompt_template(patch_central_database):
    """Test that identifier includes system_prompt_template."""
    chat_target = MagicMock()

    scorer = SelfAskTrueFalseScorer(
        chat_target=chat_target, true_false_question_path=TrueFalseQuestionPaths.GROUNDED.value
    )

    # Access identifier via get_identifier() to trigger lazy build
    sid = scorer.get_identifier()

    # Should have system_prompt_template set
    assert sid.system_prompt_template is not None
    assert len(sid.system_prompt_template) > 0


def test_self_ask_true_false_get_identifier_type(patch_central_database):
    """Test that get_identifier returns correct class_name."""
    chat_target = MagicMock()

    scorer = SelfAskTrueFalseScorer(
        chat_target=chat_target, true_false_question_path=TrueFalseQuestionPaths.GROUNDED.value
    )

    identifier = scorer.get_identifier()

    assert identifier.class_name == "SelfAskTrueFalseScorer"
    assert hasattr(identifier, "hash")
    assert hasattr(identifier, "system_prompt_template")


def test_self_ask_true_false_get_identifier_long_prompt_hashed(patch_central_database):
    """Test that long system prompts are truncated when serialized via to_dict()."""
    chat_target = MagicMock()

    scorer = SelfAskTrueFalseScorer(
        chat_target=chat_target, true_false_question_path=TrueFalseQuestionPaths.GROUNDED.value
    )

    identifier = scorer.get_identifier()

    # The identifier object itself stores the full prompt
    assert identifier.system_prompt_template is not None
    assert len(identifier.system_prompt_template) > 100  # GROUNDED prompt is long

    # But when serialized via to_dict(), long prompts are truncated
    # Format: "<first 100 chars>... [sha256:<hash[:16]>]"
    id_dict = identifier.to_dict()
    sys_prompt_in_dict = id_dict.get("system_prompt_template", "")
    if sys_prompt_in_dict:
        # If it's truncated, it will contain "... [sha256:"
        assert "[sha256:" in sys_prompt_in_dict or len(sys_prompt_in_dict) <= 100
