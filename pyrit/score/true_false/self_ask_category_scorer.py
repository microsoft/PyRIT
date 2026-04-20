# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import enum
from pathlib import Path
from typing import Dict, Optional, Union

import yaml

from pyrit.common import verify_and_resolve_path
from pyrit.common.path import SCORER_CONTENT_CLASSIFIERS_PATH
from pyrit.models import MessagePiece, Score, SeedPrompt, UnvalidatedScore
from pyrit.prompt_target import PromptChatTarget
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


class ContentClassifierPaths(enum.Enum):
    """Paths to content classifier YAML files."""

    HARMFUL_CONTENT_CLASSIFIER = Path(SCORER_CONTENT_CLASSIFIERS_PATH, "harm.yaml").resolve()
    SENTIMENT_CLASSIFIER = Path(SCORER_CONTENT_CLASSIFIERS_PATH, "sentiment.yaml").resolve()


class SelfAskCategoryScorer(TrueFalseScorer):
    """
    A class that represents a self-ask score for text classification and scoring.
    Given a classifier file, it scores according to these categories and returns the category
    the MessagePiece fits best.

    There is also a false category that is used if the MessagePiece does not fit any of the categories.
    """

    _default_validator: ScorerPromptValidator = ScorerPromptValidator()

    def __init__(
        self,
        *,
        chat_target: PromptChatTarget,
        content_classifier_path: Union[str, Path],
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
        validator: Optional[ScorerPromptValidator] = None,
    ) -> None:
        """
        Initialize a new instance of the SelfAskCategoryScorer class.

        Args:
            chat_target (PromptChatTarget): The chat target to interact with.
            content_classifier_path (Union[str, Path]): The path to the classifier YAML file.
            score_aggregator (TrueFalseAggregatorFunc): The aggregator function to use.
                Defaults to TrueFalseScoreAggregator.OR.
            validator (Optional[ScorerPromptValidator]): Custom validator. Defaults to None.
        """
        super().__init__(score_aggregator=score_aggregator, validator=validator or self._default_validator)

        self._prompt_target = chat_target

        content_classifier_path = verify_and_resolve_path(content_classifier_path)

        category_file_contents = yaml.safe_load(content_classifier_path.read_text(encoding="utf-8"))

        self._no_category_found_category = category_file_contents["no_category_found"]
        categories_as_string = self._content_classifier_to_string(category_file_contents["categories"])

        content_classifier_system_prompt_path = verify_and_resolve_path(
            SCORER_CONTENT_CLASSIFIERS_PATH / "content_classifier_system_prompt.yaml"
        )

        scoring_instructions_template = SeedPrompt.from_yaml_file(content_classifier_system_prompt_path)

        self._system_prompt = scoring_instructions_template.render_template_value(
            categories=categories_as_string,
            no_category_found=self._no_category_found_category,
        )

    def _build_identifier(self) -> None:
        """Build the scorer evaluation identifier for this scorer."""
        self._set_identifier(
            system_prompt_template=self._system_prompt,
            prompt_target=self._prompt_target,
            score_aggregator=self._score_aggregator.__name__,
        )

    def _content_classifier_to_string(self, categories: list[Dict[str, str]]) -> str:
        """
        Convert the content classifier categories to a string representation to be put in a system prompt.

        Args:
            categories (list[Dict[str, str]]): The categories to convert.

        Returns:
            str: The string representation of the categories.

        Raises:
            ValueError: If no categories are provided.
            ValueError: If the no_category_found category is not found in the provided categories.
        """
        if not categories:
            raise ValueError("Improperly formatted content classifier yaml file. No categories provided")

        category_descriptions = ""

        for category in categories:
            name = category["name"]
            desc = category["description"]

            category_descriptions += f"'{name}': {desc}\n"

        if self._no_category_found_category not in category_descriptions:
            raise ValueError(f"False category {self._no_category_found_category} not found in classifier categories")

        return category_descriptions

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        """
        Scores the given message using the chat target.

        Args:
            message_piece (MessagePiece): The message piece to score.
            objective (Optional[str]): The task based on which the text should be scored
                (the original attacker model's objective). Defaults to None.

        Returns:
            list[Score]: The message_piece's score.
                         The category that fits best in the response is used for score_category.
                         The score_value is True in all cases unless no category fits. In which case,
                         the score value is false and the _false_category is used.
        """
        unvalidated_score: UnvalidatedScore = await self._score_value_with_llm(
            prompt_target=self._prompt_target,
            system_prompt=self._system_prompt,
            message_value=message_piece.converted_value,
            message_data_type=message_piece.converted_value_data_type,
            scored_prompt_id=message_piece.id,
            objective=objective,
            attack_identifier=message_piece.attack_identifier,
        )

        score = unvalidated_score.to_score(score_value=unvalidated_score.raw_score_value, score_type="true_false")

        return [score]
