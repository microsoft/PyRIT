# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.llm_scoring import _run_llm_scoring_async
from pyrit.score.response_handler import JsonSchemaResponseHandler, ResponseHandler
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator

if TYPE_CHECKING:
    from pyrit.models import (
        ComponentIdentifier,
        JsonSchemaDefinition,
        MessagePiece,
        Score,
    )
    from pyrit.prompt_target import PromptTarget
    from pyrit.score.float_scale.numeric_scale import NumericRange


class SelfAskGeneralFloatScaleScorer(FloatScaleScorer):
    """
    A general-purpose self-ask float-scale scorer that uses a chat target and a configurable
    system prompt and prompt format. The final score is normalized to [0, 1].

    The scorer holds a chat ``chat_target`` and a ``response_handler``; the system prompt is
    rendered per-piece from ``system_prompt_format_string``.
    """

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(
        supported_data_types=["text"],
        is_objective_required=True,
    )
    TARGET_REQUIREMENTS = CHAT_TARGET_REQUIREMENTS

    def __init__(
        self,
        *,
        system_prompt_format_string: str,
        scale: NumericRange,
        chat_target: PromptTarget | None = None,
        prompt_format_string: str | None = None,
        response_handler: ResponseHandler | None = None,
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
        in the response, the category from ``scale`` will be applied.

        Args:
            system_prompt_format_string (str): System prompt template with placeholders for
                objective, prompt, and message_piece.
            scale (NumericRange): The required native score range and optional category.
            chat_target (PromptTarget | None): The chat target used to score. Must satisfy
                CHAT_TARGET_REQUIREMENTS.
            prompt_format_string (str | None): User prompt template with the same placeholders.
            response_handler (ResponseHandler | None): Parser for the target's raw output. Defaults
                to a ``JsonSchemaResponseHandler`` built from the ``*_output_key`` arguments.
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
            ValueError: If ``chat_target`` is not provided, if system_prompt_format_string is not
                provided or empty.
        """
        if chat_target is None:
            raise ValueError("A chat_target must be provided.")

        super().__init__(validator=validator or self._DEFAULT_VALIDATOR, chat_target=chat_target)
        self._prompt_target = chat_target
        if not system_prompt_format_string:
            raise ValueError("system_prompt_format_string must be provided and non-empty.")
        self._system_prompt_format_string = system_prompt_format_string
        self._prompt_format_string = prompt_format_string
        self._scale = scale
        # A caller-supplied handler owns its own response contract; otherwise the default JSON
        # handler carries the schema and enforces the numeric score contract for the round-trip.
        self._response_handler = response_handler or JsonSchemaResponseHandler(
            score_value_output_key=score_value_output_key,
            rationale_output_key=rationale_output_key,
            description_output_key=description_output_key,
            metadata_output_key=metadata_output_key,
            category_output_key=category_output_key,
            response_schema=response_json_schema,
            numeric_value=True,
        )

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
                "scale": self._scale.model_dump(),
                "response_json_schema": self._response_handler.response_schema,
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

        unvalidated = await _run_llm_scoring_async(
            chat_target=self._prompt_target,
            system_prompt=system_prompt,
            response_handler=self._response_handler,
            value=user_prompt,
            data_type=message_piece.converted_value_data_type,
            scored_prompt_id=message_piece.id,
            scorer_identifier=self.get_identifier(),
            category=self._scale.category,
            objective=objective,
        )

        score = unvalidated.to_score(
            score_value=str(
                self.scale_value_float(
                    float(unvalidated.raw_score_value),
                    self._scale.minimum_value,
                    self._scale.maximum_value,
                )
            ),
            score_type="float_scale",
        )
        return [score]
