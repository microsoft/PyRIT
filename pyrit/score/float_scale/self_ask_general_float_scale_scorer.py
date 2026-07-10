# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator

if TYPE_CHECKING:
    from pyrit.models import (
        ComponentIdentifier,
        JsonSchemaDefinition,
        MessagePiece,
        Score,
        UnvalidatedScore,
    )
    from pyrit.prompt_target import PromptTarget


class SelfAskGeneralFloatScaleScorer(FloatScaleScorer):
    """
    A general-purpose self-ask float-scale scorer that uses a chat target and a configurable
    system prompt and prompt format. The final score is normalized to [0, 1].
    """

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(
        supported_data_types=["text"],
        is_objective_required=True,
    )
    TARGET_REQUIREMENTS = CHAT_TARGET_REQUIREMENTS

    def __init__(
        self,
        *,
        chat_target: PromptTarget,
        system_prompt_format_string: str,
        prompt_format_string: str | None = None,
        category: str | None = None,
        min_value: int = 0,
        max_value: int = 100,
        validator: ScorerPromptValidator | None = None,
        score_value_output_key: str = "score_value",
        rationale_output_key: str = "rationale",
        description_output_key: str = "description",
        metadata_output_key: str = "metadata",
        category_output_key: str = "category",
        response_json_schema: JsonSchemaDefinition | None = None,
    ) -> None:
        """
        Initialize the SelfAskGeneralFloatScaleScorer.

        The target LLM must return JSON with at least the following keys:
        - score_value: a numeric value in the model's native scale (e.g., 0-100)
        - rationale: a short explanation

        Optionally it can include description, metadata, and category. If category is not provided
        in the response, the provided `category` argument will be applied.

        Args:
            chat_target (PromptTarget): The chat target used to score. Must satisfy
                CHAT_TARGET_REQUIREMENTS (multi-turn + editable history capabilities,
                possibly via normalization-pipeline adaptation).
            system_prompt_format_string (str): System prompt template with placeholders for
                objective, prompt, and message_piece.
            prompt_format_string (str | None): User prompt template with the same placeholders.
            category (str | None): Category for the score.
            min_value (int): Minimum of the model's native scale. Defaults to 0.
            max_value (int): Maximum of the model's native scale. Defaults to 100.
            validator (ScorerPromptValidator | None): Custom validator. If omitted, a default
                validator will be used requiring text input and an objective.
            score_value_output_key (str): JSON key for the score value. Defaults to "score_value".
            rationale_output_key (str): JSON key for the rationale. Defaults to "rationale".
            description_output_key (str): JSON key for the description. Defaults to "description".
            metadata_output_key (str): JSON key for the metadata. Defaults to "metadata".
            category_output_key (str): JSON key for the category. Defaults to "category".
            response_json_schema (JsonSchemaDefinition | None): An optional JSON schema constraining
                the scoring response. When provided, it is forwarded to the scoring target, which
                enforces it natively when supported or omits it via normalization. Defaults to None.

        Raises:
            ValueError: If system_prompt_format_string is not provided or empty.
            ValueError: If min_value is greater than max_value.
        """
        super().__init__(validator=validator or self._DEFAULT_VALIDATOR, chat_target=chat_target)
        self._prompt_target = chat_target
        if not system_prompt_format_string:
            raise ValueError("system_prompt_format_string must be provided and non-empty.")
        self._system_prompt_format_string = system_prompt_format_string
        self._prompt_format_string = prompt_format_string

        if min_value > max_value:
            raise ValueError("min_value must be less than or equal to max_value")

        self._score_category = category
        self._min_value = min_value
        self._max_value = max_value
        self._score_value_output_key = score_value_output_key
        self._rationale_output_key = rationale_output_key
        self._description_output_key = description_output_key
        self._metadata_output_key = metadata_output_key
        self._category_output_key = category_output_key
        self._response_json_schema = response_json_schema

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier for this scorer.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        return self._create_identifier(
            params={
                "system_prompt_template": self._system_prompt_format_string,
                "user_prompt_template": self._prompt_format_string,
                "min_value": self._min_value,
                "max_value": self._max_value,
                "response_json_schema": self._response_json_schema,
            },
            prompt_target=self._prompt_target.get_identifier(),
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        """
        Score a single message piece using the configured prompts and scale to [0, 1].

        Args:
            message_piece (MessagePiece): The piece to score.
            objective (str, optional): Context objective for the scoring.

        Returns:
            list[Score]: A list with a single float-scale score in [0, 1].
        """
        original_prompt = message_piece.converted_value

        # Render system prompt and user prompt
        system_prompt = self._system_prompt_format_string.format(
            objective=objective,
            prompt=original_prompt,
            message_piece=message_piece,
        )

        user_prompt = original_prompt
        if self._prompt_format_string:
            user_prompt = self._prompt_format_string.format(
                objective=objective,
                prompt=original_prompt,
                message_piece=message_piece,
            )

        unvalidated: UnvalidatedScore = await self._score_value_with_llm_async(
            prompt_target=self._prompt_target,
            system_prompt=system_prompt,
            message_value=user_prompt,
            message_data_type=message_piece.converted_value_data_type,
            scored_prompt_id=message_piece.id,
            category=self._score_category,
            objective=objective,
            score_value_output_key=self._score_value_output_key,
            rationale_output_key=self._rationale_output_key,
            description_output_key=self._description_output_key,
            metadata_output_key=self._metadata_output_key,
            category_output_key=self._category_output_key,
            response_json_schema=self._response_json_schema,
            numeric_value=True,
        )

        score = unvalidated.to_score(
            score_value=str(
                self.scale_value_float(float(unvalidated.raw_score_value), self._min_value, self._max_value)
            ),
            score_type="float_scale",
        )
        return [score]
