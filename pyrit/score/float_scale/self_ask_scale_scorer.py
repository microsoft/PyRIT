# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import enum
from pathlib import Path

from pyrit.common.path import SCORER_SCALES_PATH
from pyrit.models import (
    ComponentIdentifier,
    JsonSchemaDefinition,
    MessagePiece,
    Score,
    SeedPrompt,
)
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS, PromptTarget
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.float_scale.numeric_scale import NumericRubric
from pyrit.score.llm_scoring import _run_llm_scoring_async
from pyrit.score.response_handler import JsonSchemaResponseHandler, ResponseHandler
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.system_prompt import _render_system_prompt_template

_DEFAULT_SCALE_PATH = Path(SCORER_SCALES_PATH, "tree_of_attacks_scale.yaml").resolve()
_DEFAULT_SCALE_SYSTEM_PROMPT_PATH = Path(SCORER_SCALES_PATH, "general_system_prompt.yaml").resolve()


def render_scale_system_prompt(
    *,
    scale: NumericRubric,
    system_prompt_template: SeedPrompt | str | None = None,
) -> SeedPrompt:
    """
    Render a numeric-scale scoring system prompt from a scale and a template.

    The bundled general template is used when ``system_prompt_template`` is omitted. A supplied
    ``SeedPrompt`` preserves metadata such as ``response_json_schema``; a string is treated as an
    inline Jinja template.

    Args:
        scale (NumericRubric): The rubric supplying prompt parameters and normalization bounds.
        system_prompt_template (SeedPrompt | str | None): A custom template or the bundled default.

    Returns:
        SeedPrompt: A rendered copy of the template with its ``value`` populated.
    """
    return _render_system_prompt_template(
        system_prompt_template=system_prompt_template,
        default_template_path=_DEFAULT_SCALE_SYSTEM_PROMPT_PATH,
        render_params=scale.render_params,
        required_parameters=["minimum_value", "maximum_value"],
    )


class SelfAskScaleScorer(FloatScaleScorer):
    """
    A "self-ask" scorer for text scoring on a customizable numeric scale.

    The scorer holds a ``chat_target``, a rendered or static ``system_prompt``, a ``NumericRubric``
    defining normalization and category, and a ``response_handler``. Use ``from_scale`` to render a
    template and configure the scorer from one rubric object.
    """

    class ScalePaths(enum.Enum):
        """Enum containing paths to numeric scale YAML configuration files."""

        TREE_OF_ATTACKS_SCALE = Path(SCORER_SCALES_PATH, "tree_of_attacks_scale.yaml").resolve()
        TASK_ACHIEVED_SCALE = Path(SCORER_SCALES_PATH, "task_achieved_scale.yaml").resolve()
        CRITERIA_SCALE = Path(SCORER_SCALES_PATH, "criteria_example_scale.yaml").resolve()

    class SystemPaths(enum.Enum):
        """Enum containing paths to system prompt YAML configuration files."""

        GENERAL_SYSTEM_PROMPT = Path(SCORER_SCALES_PATH, "general_system_prompt.yaml").resolve()
        RED_TEAMER_SYSTEM_PROMPT = Path(SCORER_SCALES_PATH, "red_teamer_system_prompt.yaml").resolve()
        CRITERIA_SYSTEM_PROMPT = Path(SCORER_SCALES_PATH, "criteria_system_prompt.yaml").resolve()

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(
        supported_data_types=["text"],
        is_objective_required=True,
    )
    TARGET_REQUIREMENTS = CHAT_TARGET_REQUIREMENTS

    def __init__(
        self,
        *,
        chat_target: PromptTarget | None = None,
        system_prompt: SeedPrompt | str,
        scale: NumericRubric,
        response_handler: ResponseHandler | None = None,
        validator: ScorerPromptValidator | None = None,
    ) -> None:
        """
        Initialize the SelfAskScaleScorer.

        Args:
            chat_target (PromptTarget | None): The chat target used for scoring. Must satisfy
                CHAT_TARGET_REQUIREMENTS.
            system_prompt (SeedPrompt | str): The rendered or static scoring system prompt.
            scale (NumericRubric): The rubric defining score normalization and category.
            response_handler (ResponseHandler | None): Parser for the target's raw output. Defaults
                to ``JsonSchemaResponseHandler``.
            validator (ScorerPromptValidator | None): Custom validator for the scorer. Defaults to
                None.

        Raises:
            ValueError: If ``chat_target`` is not provided.
        """
        if chat_target is None:
            raise ValueError("A chat_target must be provided.")

        super().__init__(validator=validator or self._DEFAULT_VALIDATOR, chat_target=chat_target)
        self._prompt_target = chat_target

        self._system_prompt, schema = self._resolve_system_prompt(system_prompt)
        self._scale = scale

        # When the caller does not supply a response handler, the default JSON handler carries the
        # schema (if any) declared by the system prompt and enforces the numeric score contract, so
        # the round-trip forwards the schema to the scoring target. A caller-supplied handler owns
        # its own response contract.
        self._response_handler = response_handler or JsonSchemaResponseHandler(
            response_schema=schema, numeric_value=True
        )

    @classmethod
    def from_scale(
        cls,
        *,
        chat_target: PromptTarget,
        scale: NumericRubric | None = None,
        system_prompt_template: SeedPrompt | str | None = None,
        response_handler: ResponseHandler | None = None,
        validator: ScorerPromptValidator | None = None,
    ) -> "SelfAskScaleScorer":
        """
        Build a scorer whose prompt and normalization are driven by one ``NumericRubric``.

        When ``scale`` is omitted, the bundled tree-of-attacks scale is used. The supplied scale is
        rendered through the bundled template or ``system_prompt_template`` and is also stored on the
        scorer for normalization, preventing prompt bounds from being configured separately.

        Args:
            chat_target (PromptTarget): The chat target used for scoring.
            scale (NumericRubric | None): The rubric to use. Defaults to the bundled tree-of-attacks
                rubric.
            system_prompt_template (SeedPrompt | str | None): A custom Jinja template or the bundled
                general template.
            response_handler (ResponseHandler | None): Parser for the target's raw output. Defaults
                to None (uses ``JsonSchemaResponseHandler``).
            validator (ScorerPromptValidator | None): Custom validator. Defaults to None.

        Returns:
            SelfAskScaleScorer: The constructed scorer.
        """
        resolved_scale = scale or NumericRubric.from_yaml(_DEFAULT_SCALE_PATH)
        system_prompt = render_scale_system_prompt(
            scale=resolved_scale,
            system_prompt_template=system_prompt_template,
        )
        return cls(
            chat_target=chat_target,
            system_prompt=system_prompt,
            scale=resolved_scale,
            response_handler=response_handler,
            validator=validator,
        )

    @staticmethod
    def _resolve_system_prompt(
        system_prompt: SeedPrompt | str,
    ) -> tuple[str, JsonSchemaDefinition | None]:
        if isinstance(system_prompt, SeedPrompt):
            return system_prompt.value, system_prompt.response_json_schema
        if isinstance(system_prompt, str):
            return system_prompt, None
        raise TypeError("system_prompt must be a SeedPrompt or str.")

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier for this scorer.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        return self._create_identifier(
            params={
                "system_prompt_template": self._system_prompt,
                "user_prompt_template": "objective: {objective}\nresponse: {response}",
                "scale": self._scale.model_dump(exclude_none=True),
                "response_json_schema": self._response_handler.response_schema,
            },
            prompt_target=self._prompt_target.get_identifier(),
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        """
        Scores the given message_piece using "self-ask" for the chat target.

        Args:
            message_piece (MessagePiece): The message piece containing the content to be scored.
                Supports text and non-text types (e.g., image_path). For non-text content,
                the objective context is sent as a prepended text piece alongside the raw content.
            objective (str): The objective based on which the content should be scored (the original
                attacker model's objective).

        Returns:
            list[Score]: The message piece's score.
                         The score_value is a value from [0,1] that is scaled based on the scorer's scale.
        """
        # For non-text content (images, audio, etc.), send the raw content with its original
        # data type and prepend the objective as a text piece. This allows multimodal LLMs
        # to evaluate the content directly (e.g., viewing an image to assess it).
        is_non_text = message_piece.converted_value_data_type != "text"
        if is_non_text:
            prepended_text = f"objective: {objective}\nresponse:"
            scoring_value = message_piece.converted_value
            scoring_data_type = message_piece.converted_value_data_type
        else:
            prepended_text = None
            scoring_value = f"objective: {objective}\nresponse: {message_piece.converted_value}"
            scoring_data_type = "text"

        unvalidated_score = await _run_llm_scoring_async(
            chat_target=self._prompt_target,
            system_prompt=self._system_prompt,
            response_handler=self._response_handler,
            value=scoring_value,
            data_type=scoring_data_type,
            scored_prompt_id=message_piece.id,
            scorer_identifier=self.get_identifier(),
            prepended_text=prepended_text,
            category=self._scale.category,
            objective=objective,
        )

        score = unvalidated_score.to_score(
            score_value=str(
                self.scale_value_float(
                    float(unvalidated_score.raw_score_value),
                    self._scale.minimum_value,
                    self._scale.maximum_value,
                )
            ),
            score_type="float_scale",
        )

        return [score]
