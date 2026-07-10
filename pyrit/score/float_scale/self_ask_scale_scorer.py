# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import enum
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from pyrit.common import verify_and_resolve_path
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
from pyrit.score.llm_scoring import _run_llm_scoring_async
from pyrit.score.response_handler import JsonSchemaResponseHandler, ResponseHandler
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator

_DEFAULT_SCALE_ARGUMENTS_PATH = Path(SCORER_SCALES_PATH, "tree_of_attacks_scale.yaml").resolve()
_DEFAULT_SCALE_SYSTEM_PROMPT_PATH = Path(SCORER_SCALES_PATH, "general_system_prompt.yaml").resolve()


def _validate_scale_arguments(scale_args: dict[str, Any]) -> None:
    try:
        minimum_value = scale_args["minimum_value"]
        maximum_value = scale_args["maximum_value"]
        category = scale_args["category"]
    except KeyError as e:
        raise ValueError(f"Missing key in scale_args: {e.args[0]}") from None

    if not isinstance(minimum_value, int):
        raise ValueError(f"Minimum value must be an integer, got {type(minimum_value).__name__}.")
    if not isinstance(maximum_value, int):
        raise ValueError(f"Maximum value must be an integer, got {type(maximum_value).__name__}.")
    if minimum_value > maximum_value:
        raise ValueError("Minimum value must be less than or equal to the maximum value.")
    if not category:
        raise ValueError("Category must be set and cannot be empty.")


def load_scale_arguments(scale_arguments_path: Path | str | None = None) -> dict[str, Any]:
    """
    Load and validate a scale-arguments YAML file (min/max/category and rendering parameters).

    Exposed publicly so consumers can build non-default ``SelfAskScaleScorer`` instances (or drive
    ``from_scale_arguments``) without reaching into private helpers.

    Args:
        scale_arguments_path (Path | str | None): Path to the scale-arguments YAML file. Defaults to
            the bundled tree-of-attacks scale.

    Returns:
        dict[str, Any]: The validated scale arguments.
    """
    resolved_path = verify_and_resolve_path(scale_arguments_path or _DEFAULT_SCALE_ARGUMENTS_PATH)
    scale_args = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
    _validate_scale_arguments(scale_args)
    return scale_args


def render_scale_system_prompt(
    *,
    scale_args: Mapping[str, Any],
    system_prompt_path: Path | str | None = None,
) -> SeedPrompt:
    """
    Render a numeric-scale scoring system prompt from scale arguments and a template.

    Loads the templated system-prompt ``SeedPrompt`` (defaulting to the bundled
    ``general_system_prompt.yaml``) and renders it with the scale arguments (``minimum_value``,
    ``maximum_value``, ``category`` and their descriptions). The returned ``SeedPrompt`` is a copy
    whose ``value`` is the rendered text; the template's other fields (notably
    ``response_json_schema``) are preserved so schema forwarding keeps working.

    Args:
        scale_args (Mapping[str, Any]): The scale arguments supplying the render parameters.
        system_prompt_path (Path | str | None): Path to the system-prompt template YAML. Defaults to
            the bundled general scale system prompt.

    Returns:
        SeedPrompt: A rendered copy of the template with its ``value`` populated.
    """
    resolved_path = verify_and_resolve_path(
        system_prompt_path if system_prompt_path else _DEFAULT_SCALE_SYSTEM_PROMPT_PATH
    )
    template = SeedPrompt.from_yaml_file(resolved_path)
    rendered_value = template.render_template_value(**scale_args)
    return template.model_copy(update={"value": rendered_value})


class SelfAskScaleScorer(FloatScaleScorer):
    """
    A "self-ask" scorer for text scoring on a customizable numeric scale.

    The scorer holds a ``chat_target``, a ``system_prompt`` (a rendered or static ``SeedPrompt`` or a
    plain ``str``), and a ``response_handler`` that turns the target's raw output into a score. Render
    a scale system prompt with ``render_scale_system_prompt``, or use ``from_scale_arguments`` to
    build the system prompt, min/max and category directly from a scale-arguments YAML file.
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
        system_prompt: SeedPrompt | str | None = None,
        response_handler: ResponseHandler | None = None,
        min_value: int = 1,
        max_value: int = 10,
        score_category: str | None = None,
        validator: ScorerPromptValidator | None = None,
    ) -> None:
        """
        Initialize the SelfAskScaleScorer.

        Args:
            chat_target (PromptTarget | None): The chat target used for scoring. Must satisfy
                CHAT_TARGET_REQUIREMENTS.
            system_prompt (SeedPrompt | str | None): The scoring system prompt. A ``SeedPrompt``
                (e.g. rendered via ``render_scale_system_prompt``) is used verbatim and may carry a
                ``response_json_schema``; a ``str`` is used as-is; ``None`` falls back to the default
                tree-of-attacks scale rendered with the general system prompt. Defaults to None.
            response_handler (ResponseHandler | None): Parser for the target's raw output. Defaults
                to ``JsonSchemaResponseHandler``.
            min_value (int): Minimum of the model's native scale, used when ``system_prompt`` is
                provided explicitly. Defaults to 1.
            max_value (int): Maximum of the model's native scale, used when ``system_prompt`` is
                provided explicitly. Defaults to 10.
            score_category (str | None): Category to attach to scores when ``system_prompt`` is
                provided explicitly. Defaults to None.
            validator (ScorerPromptValidator | None): Custom validator for the scorer. Defaults to
                None.

        Raises:
            ValueError: If ``chat_target`` is not provided.
        """
        if chat_target is None:
            raise ValueError("A chat_target must be provided.")

        super().__init__(validator=validator or self._DEFAULT_VALIDATOR, chat_target=chat_target)
        self._prompt_target = chat_target

        if system_prompt is None:
            (
                self._system_prompt,
                schema,
                self._minimum_value,
                self._maximum_value,
                self._category,
            ) = self._build_from_scale_arguments(None, None)
        else:
            rendered_value, schema = self._resolve_system_prompt(system_prompt)
            self._system_prompt = rendered_value
            self._minimum_value = min_value
            self._maximum_value = max_value
            self._category = score_category

        # When the caller does not supply a response handler, the default JSON handler carries the
        # schema (if any) declared by the system prompt and enforces the numeric score contract, so
        # the round-trip forwards the schema to the scoring target. A caller-supplied handler owns
        # its own response contract.
        self._response_handler = response_handler or JsonSchemaResponseHandler(
            response_schema=schema, numeric_value=True
        )

    @classmethod
    def from_scale_arguments(
        cls,
        *,
        chat_target: PromptTarget,
        scale_arguments_path: Path | str | None = None,
        system_prompt_path: Path | str | None = None,
        response_handler: ResponseHandler | None = None,
        validator: ScorerPromptValidator | None = None,
    ) -> "SelfAskScaleScorer":
        """
        Build a scorer whose system prompt, min/max and category are driven by a scale-arguments YAML.

        Loads the scale definition (``minimum_value``, ``maximum_value``, ``category`` and rendering
        parameters) from ``scale_arguments_path`` and renders the numeric-scale system prompt via
        ``render_scale_system_prompt``.

        Args:
            chat_target (PromptTarget): The chat target used for scoring.
            scale_arguments_path (Path | str | None): Path to the scale-arguments YAML file (e.g. a
                ``SelfAskScaleScorer.ScalePaths`` value). Defaults to the tree-of-attacks scale.
            system_prompt_path (Path | str | None): Path to the system-prompt template YAML (e.g. a
                ``SelfAskScaleScorer.SystemPaths`` value). Defaults to the general system prompt.
            response_handler (ResponseHandler | None): Parser for the target's raw output. Defaults
                to None (uses ``JsonSchemaResponseHandler``).
            validator (ScorerPromptValidator | None): Custom validator. Defaults to None.

        Returns:
            SelfAskScaleScorer: The constructed scorer.
        """
        scale_args = load_scale_arguments(scale_arguments_path)
        system_prompt = render_scale_system_prompt(scale_args=scale_args, system_prompt_path=system_prompt_path)
        return cls(
            chat_target=chat_target,
            system_prompt=system_prompt,
            response_handler=response_handler,
            min_value=scale_args["minimum_value"],
            max_value=scale_args["maximum_value"],
            score_category=scale_args["category"],
            validator=validator,
        )

    @staticmethod
    def _build_from_scale_arguments(
        scale_arguments_path: Path | str | None,
        system_prompt_path: Path | str | None,
    ) -> tuple[str, JsonSchemaDefinition | None, int, int, str]:
        scale_args = load_scale_arguments(scale_arguments_path)
        rendered = render_scale_system_prompt(scale_args=scale_args, system_prompt_path=system_prompt_path)
        # Optional JSON schema embedded in the system prompt YAML. Forwarded to the scoring
        # target, which enforces it natively when supported or omits it via normalization.
        return (
            rendered.value,
            rendered.response_json_schema,
            scale_args["minimum_value"],
            scale_args["maximum_value"],
            scale_args["category"],
        )

    @staticmethod
    def _resolve_system_prompt(
        system_prompt: SeedPrompt | str,
    ) -> tuple[str, JsonSchemaDefinition | None]:
        if isinstance(system_prompt, SeedPrompt):
            return system_prompt.value, system_prompt.response_json_schema
        if isinstance(system_prompt, str):
            return system_prompt, None
        raise TypeError("system_prompt must be a SeedPrompt, str, or None.")

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
            category=self._category,
            objective=objective,
        )

        score = unvalidated_score.to_score(
            score_value=str(
                self.scale_value_float(
                    float(unvalidated_score.raw_score_value), self._minimum_value, self._maximum_value
                )
            ),
            score_type="float_scale",
        )

        return [score]

    def _validate_scale_arguments_set(self, scale_args: dict[str, Any]) -> None:
        _validate_scale_arguments(scale_args)
