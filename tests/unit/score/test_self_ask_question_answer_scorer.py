# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.models import ComponentIdentifier, MessagePiece, Score, UnvalidatedScore
from pyrit.prompt_target import PromptTarget
from pyrit.score.true_false.self_ask_question_answer_scorer import SelfAskQuestionAnswerScorer


@pytest.fixture
def mock_chat_target(patch_central_database):
    return MagicMock(spec=PromptTarget)


async def test_score_async_returns_score_from_unvalidated(mock_chat_target):
    scorer = SelfAskQuestionAnswerScorer(chat_target=mock_chat_target)

    unvalidated = UnvalidatedScore(
        raw_score_value="True",
        score_value_description="answer matches",
        score_category=["question_answering"],
        score_rationale="the response matches the expected answer",
        score_metadata=None,
        scorer_class_identifier=ComponentIdentifier(
            class_name="SelfAskQuestionAnswerScorer",
            class_module="pyrit.score",
        ),
        message_piece_id="abc",
        objective="2+2=?\nanswer: 4",
    )

    message = MessagePiece(role="assistant", original_value="4").to_message()
    with patch.object(scorer._memory, "add_scores_to_memory", new=MagicMock()):
        with patch.object(scorer, "_score_value_with_llm_async", new=AsyncMock(return_value=unvalidated)):
            scores = await scorer.score_async(message, objective="2+2=?\nanswer: 4")

    assert len(scores) == 1
    assert isinstance(scores[0], Score)
    assert scores[0].score_type == "true_false"
    assert scores[0].get_value() is True
