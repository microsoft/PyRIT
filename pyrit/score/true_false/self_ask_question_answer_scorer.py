# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.common.utils import verify_and_resolve_path
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.self_ask_true_false_scorer import SelfAskTrueFalseScorer
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)

if TYPE_CHECKING:
    import pathlib

    from pyrit.models import MessagePiece, Score, UnvalidatedScore
    from pyrit.prompt_target import PromptChatTarget


class SelfAskQuestionAnswerScorer(SelfAskTrueFalseScorer):
    """
    A class that represents a self-ask question answering scorer.

    Usually, QuestionAnswerScorer should be used, but this can be useful for when choices are not sent
    to objective target or you need more flexibility in determining if the questions were answered correctly.
    """

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(
        supported_data_types=["text"],
        is_objective_required=True,
    )

    def __init__(
        self,
        *,
        chat_target: PromptChatTarget,
        true_false_question_path: Optional[pathlib.Path] = None,
        validator: Optional[ScorerPromptValidator] = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """
        Initialize the SelfAskQuestionAnswerScorer object.

        Args:
            chat_target (PromptChatTarget): The chat target to use for the scorer.
            true_false_question_path (Optional[pathlib.Path]): The path to the true/false question file.
                Defaults to None, which uses the default question_answering.yaml file.
            validator (Optional[ScorerPromptValidator]): Custom validator. Defaults to None.
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use.
                Defaults to TrueFalseScoreAggregator.OR.
        """
        if not true_false_question_path:
            true_false_question_path = SCORER_SEED_PROMPT_PATH / "true_false_question" / "question_answering.yaml"

        true_false_question_path = verify_and_resolve_path(true_false_question_path)

        super().__init__(
            chat_target=chat_target,
            true_false_question_path=true_false_question_path,
            validator=validator,
            score_aggregator=score_aggregator,
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        """
        Score the message piece using question answering evaluation.

        Args:
            message_piece (MessagePiece): The answer given by the target to be scored.
            objective (Optional[str]): The objective, which usually contains the question and the correct answer.
                Defaults to None.

        Returns:
            list[Score]: A list containing a single Score object representing whether the answer was correct.
        """
        prompt = (
            f"{objective}\n\nThe target response is as follows. "
            f"Evaluate if the response is correct:\n{message_piece.converted_value}"
        )

        unvalidated_score: UnvalidatedScore = await self._score_value_with_llm(
            prompt_target=self._prompt_target,
            system_prompt=self._system_prompt,
            message_value=prompt,
            message_data_type="text",
            scored_prompt_id=message_piece.id,
            category=self._score_category,
            objective=objective,
            attack_identifier=message_piece.attack_identifier,
        )

        score = unvalidated_score.to_score(score_value=unvalidated_score.raw_score_value, score_type="true_false")

        return [score]
