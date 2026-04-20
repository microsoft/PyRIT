# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import enum
from pathlib import Path
from typing import Any, Iterator, Optional, Union

import yaml

from pyrit.common import verify_and_resolve_path
from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.models import MessagePiece, Score, SeedPrompt
from pyrit.prompt_target import PromptChatTarget
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

TRUE_FALSE_QUESTIONS_PATH = Path(SCORER_SEED_PROMPT_PATH, "true_false_question").resolve()


class TrueFalseQuestionPaths(enum.Enum):
    """Paths to true/false question YAML files."""

    CURRENT_EVENTS = Path(TRUE_FALSE_QUESTIONS_PATH, "current_events.yaml").resolve()
    GROUNDED = Path(TRUE_FALSE_QUESTIONS_PATH, "grounded.yaml").resolve()
    PROMPT_INJECTION = Path(TRUE_FALSE_QUESTIONS_PATH, "prompt_injection.yaml").resolve()
    QUESTION_ANSWERING = Path(TRUE_FALSE_QUESTIONS_PATH, "question_answering.yaml").resolve()
    GANDALF = Path(TRUE_FALSE_QUESTIONS_PATH, "gandalf.yaml").resolve()
    YES_NO = Path(TRUE_FALSE_QUESTIONS_PATH, "yes_no_answer.yaml").resolve()
    TASK_ACHIEVED = Path(TRUE_FALSE_QUESTIONS_PATH, "task_achieved.yaml").resolve()


class TrueFalseQuestion:
    """
    A class that represents a true/false question.

    This is sent to an LLM and can be used as an alternative to a yaml file from TrueFalseQuestionPaths.
    """

    def __init__(self, *, true_description: str, false_description: str = "", category: str = "", metadata: str = ""):
        """
        Initialize a TrueFalseQuestion instance.

        Args:
            true_description (str): Description of what constitutes a "true" response.
            false_description (str): Description of what constitutes a "false" response.
                Defaults to a generic description if not provided.
            category (str): The category of the question. Defaults to an empty string.
            metadata (str): Additional metadata for context. Defaults to an empty string.
        """
        self.true_description = true_description

        self.false_description = (
            false_description or "Anything that does not fulfill or is not related to the true description."
        )

        self.category = category
        self.metadata = metadata

        self._keys = ["category", "true_description", "false_description"]

    def __getitem__(self, key: str) -> Any:
        """Return the value of the specified key."""
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        """Set the value of the specified key."""
        setattr(self, key, value)

    def __iter__(self) -> Iterator[str]:
        """Return an iterator over the keys."""
        # Define which keys should be included when iterating
        return iter(self._keys)


class SelfAskTrueFalseScorer(TrueFalseScorer):
    """A class that represents a self-ask true/false for scoring."""

    _default_validator: ScorerPromptValidator = ScorerPromptValidator(
        supported_data_types=["text", "image_path"],
    )

    def __init__(
        self,
        *,
        chat_target: PromptChatTarget,
        true_false_question_path: Optional[Union[str, Path]] = None,
        true_false_question: Optional[TrueFalseQuestion] = None,
        true_false_system_prompt_path: Optional[Union[str, Path]] = None,
        validator: Optional[ScorerPromptValidator] = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """
        Initialize the SelfAskTrueFalseScorer.

        Args:
            chat_target (PromptChatTarget): The chat target to interact with.
            true_false_question_path (Optional[Union[str, Path]]): The path to the true/false question file.
            true_false_question (Optional[TrueFalseQuestion]): The true/false question object.
            true_false_system_prompt_path (Optional[Union[str, Path]]): The path to the system prompt file.
            validator (Optional[ScorerPromptValidator]): Custom validator. Defaults to None.
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use.
                Defaults to TrueFalseScoreAggregator.OR.

        Raises:
            ValueError: If neither true_false_question_path nor true_false_question is provided.
            ValueError: If both true_false_question_path and true_false_question are provided.
            ValueError: If required keys are missing in true_false_question.
        """
        super().__init__(validator=validator or self._default_validator, score_aggregator=score_aggregator)

        self._prompt_target = chat_target

        if not true_false_question_path and not true_false_question:
            raise ValueError("Either true_false_question_path or true_false_question must be provided.")
        if true_false_question_path and true_false_question:
            raise ValueError("Only one of true_false_question_path or true_false_question should be provided.")

        true_false_system_prompt_path = (
            true_false_system_prompt_path
            if true_false_system_prompt_path
            else TRUE_FALSE_QUESTIONS_PATH / "true_false_system_prompt.yaml"
        )

        true_false_system_prompt_path = verify_and_resolve_path(true_false_system_prompt_path)

        if true_false_question_path:
            true_false_question_path = verify_and_resolve_path(true_false_question_path)
            true_false_question = yaml.safe_load(true_false_question_path.read_text(encoding="utf-8"))

        for key in ["category", "true_description", "false_description"]:
            if key not in true_false_question:
                raise ValueError(f"{key} must be provided in true_false_question.")

        self._score_category = true_false_question["category"]
        true_category = true_false_question["true_description"]
        false_category = true_false_question["false_description"]

        metadata = true_false_question["metadata"] if "metadata" in true_false_question else ""

        scoring_instructions_template = SeedPrompt.from_yaml_file(true_false_system_prompt_path)

        self._system_prompt = scoring_instructions_template.render_template_value(
            true_description=true_category, false_description=false_category, metadata=metadata
        )

    def _build_identifier(self) -> None:
        """Build the scorer evaluation identifier for this scorer."""
        self._set_identifier(
            system_prompt_template=self._system_prompt,
            user_prompt_template="objective: {objective}\nresponse: {response}",
            prompt_target=self._prompt_target,
            score_aggregator=self._score_aggregator.__name__,
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        """
        Scores the given message piece using "self-ask" for the chat target.

        Args:
            message_piece (MessagePiece): The message piece containing the text or image to be scored.
            objective (Optional[str]): The objective to evaluate against (the original attacker model's objective).
                Defaults to None.

        Returns:
            list[Score]: A list containing a single Score object.
                The category is configured from the TrueFalseQuestionPath.
                The score_value is True or False based on which description fits best.
                Metadata can be configured to provide additional information.
        """
        # Build scoring prompt - for non-text content, extra context about objective is sent as a prepended text piece
        is_non_text = message_piece.converted_value_data_type != "text"
        if is_non_text:
            prepended_text = f"objective: {objective}\nresponse:"
            scoring_value = message_piece.converted_value
            scoring_data_type = message_piece.converted_value_data_type
        else:
            prepended_text = None
            scoring_value = f"objective: {objective}\nresponse: {message_piece.converted_value}"
            scoring_data_type = "text"

        unvalidated_score = await self._score_value_with_llm(
            prompt_target=self._prompt_target,
            system_prompt=self._system_prompt,
            message_value=scoring_value,
            message_data_type=scoring_data_type,
            scored_prompt_id=message_piece.id,
            prepended_text_message_piece=prepended_text,
            category=self._score_category,
            objective=objective,
            attack_identifier=message_piece.attack_identifier,
        )

        score = unvalidated_score.to_score(score_value=unvalidated_score.raw_score_value, score_type="true_false")
        return [score]
