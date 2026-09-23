# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.models import AnswerMatches, Condition, ScoringExpectation
from pyrit.score.llm_scoring import _run_llm_scoring_async
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.self_ask_true_false_scorer import (
    SelfAskTrueFalseScorer,
    TrueFalseQuestion,
    render_true_false_system_prompt,
)
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)

if TYPE_CHECKING:
    import pathlib

    from pyrit.models import ComponentIdentifier, MessagePiece, Score
    from pyrit.prompt_target import PromptTarget
    from pyrit.score.response_handler import ResponseHandler


class SelfAskQuestionAnswerScorer(SelfAskTrueFalseScorer):
    """
    A class that represents a self-ask question answering scorer.

    Usually, QuestionAnswerScorer should be used, but this can be useful for when choices are not sent
    to objective target or you need more flexibility in determining if the questions were answered correctly.

    Breaking change: requires an ``AnswerMatches`` condition. Objective text supplies question
    context only; use ``SelfAskTrueFalseScorer`` for independent objective evaluation.
    """

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(
        supported_data_types=["text"],
    )
    MATCHED_CONDITIONS = frozenset({AnswerMatches})
    REQUIRED_CONDITIONS = MATCHED_CONDITIONS
    _ANSWER_CONDITION_VERSION = 2
    _TYPED_ANSWER_PROMPT = (
        "Question or context: {objective}\n"
        'The correct answer is "{correct_answer}".\n'
        "Evaluate against this correct answer. Use the question or context to interpret the answer, "
        "not as a separate success criterion."
    )
    _JUDGMENT_PROMPT = (
        "{objective}\n\nThe target response is as follows. Evaluate if the response is correct:\n{response}"
    )

    def __init__(
        self,
        *,
        chat_target: PromptTarget,
        true_false_question_path: pathlib.Path | None = None,
        response_handler: ResponseHandler | None = None,
        validator: ScorerPromptValidator | None = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """
        Initialize the SelfAskQuestionAnswerScorer object.

        Args:
            chat_target (PromptTarget): The chat target to use for the scorer. Must satisfy
                CHAT_TARGET_REQUIREMENTS (multi-turn + editable history capabilities,
                possibly via normalization-pipeline adaptation).
            true_false_question_path (pathlib.Path | None): The path to the true/false question file.
                Defaults to None, which uses the default question_answering.yaml file.
            response_handler (ResponseHandler | None): Parser for the target's raw output. Defaults
                to None (uses ``JsonSchemaResponseHandler``).
            validator (ScorerPromptValidator | None): Custom validator. Defaults to None.
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use.
                Defaults to TrueFalseScoreAggregator.OR.
        """
        if not true_false_question_path:
            true_false_question_path = SCORER_SEED_PROMPT_PATH / "true_false_question" / "question_answering.yaml"

        question = TrueFalseQuestion.from_yaml(true_false_question_path)
        system_prompt = render_true_false_system_prompt(question=question)

        super().__init__(
            chat_target=chat_target,
            system_prompt=system_prompt,
            question=question,
            response_handler=response_handler,
            validator=validator,
            score_aggregator=score_aggregator,
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Identify the typed-answer prompt contract as well as the judge configuration.

        Returns:
            ComponentIdentifier: The versioned scorer configuration.
        """
        return self._create_identifier(
            params={
                "system_prompt_template": self._system_prompt,
                "user_prompt_template": self._JUDGMENT_PROMPT,
                "typed_answer_template": self._TYPED_ANSWER_PROMPT,
                "answer_condition_version": self._ANSWER_CONDITION_VERSION,
                "question": self._question.model_dump(),
                "response_json_schema": self._response_handler.json_response_config.json_schema,
            },
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
            prompt_target=self._prompt_target.get_identifier(),
        )

    def _judgment_replay_identifier(self) -> dict[str, object]:
        """
        Version typed-answer prompting while retaining the shared true/false conversion.

        Returns:
            dict[str, object]: The versioned judgment contract.
        """
        return {**super()._judgment_replay_identifier(), "answer_condition_version": self._ANSWER_CONDITION_VERSION}

    def matched_conditions(self) -> frozenset[type[Condition]]:
        """Return the answer criterion, regardless of the message validator configuration."""
        return self.MATCHED_CONDITIONS

    def required_conditions(self) -> frozenset[type[Condition]]:
        """Return the required answer criterion."""
        return self.REQUIRED_CONDITIONS

    def _validate_expectation(self, *, expectation: ScoringExpectation | None) -> None:
        """
        Require typed ground truth, including on deprecated message entry points.

        Raises:
            ValueError: If the required answer condition is absent.
        """
        ScoringExpectation.validate_type(expectation)
        if expectation is None or not any(isinstance(item, AnswerMatches) for item in expectation.conditions):
            raise ValueError(
                "SelfAskQuestionAnswerScorer requires an AnswerMatches condition. "
                "Objective-only Q&A scoring is no longer supported. "
                "Supply AnswerMatches(correct_answer=...) or use SelfAskTrueFalseScorer to evaluate an objective."
            )
        super()._validate_expectation(expectation=expectation)

    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        """
        Construct the judge question from typed ground truth, without changing the evidence.

        Returns:
            list[Score]: The judge's true/false scores.

        Raises:
            ValueError: If the required answer condition is absent or duplicated.
        """
        self._validate_expectation(expectation=expectation)
        conditions = expectation.conditions if expectation else ()
        answer = next(item for item in conditions if isinstance(item, AnswerMatches))
        objective = expectation.objective if expectation else None
        correct_answer = (
            f"{answer.correct_answer_index}: {answer.correct_answer}"
            if answer.correct_answer_index
            else answer.correct_answer
        )
        objective = self._TYPED_ANSWER_PROMPT.format(
            objective=objective or "Not provided",
            correct_answer=correct_answer,
        )
        prompt = self._JUDGMENT_PROMPT.format(objective=objective, response=message_piece.converted_value)

        unvalidated_score = await _run_llm_scoring_async(
            chat_target=self._prompt_target,
            system_prompt=self._system_prompt,
            response_handler=self._response_handler,
            value=prompt,
            data_type="text",
            scored_prompt_id=message_piece.id,
            scorer_identifier=self.get_identifier(),
            judgment_replay_identifier=self._get_judgment_replay_identifier(),
            category=self._score_category,
        )

        return [self._convert_score(unvalidated_score)]
