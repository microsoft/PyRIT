# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import enum
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from pyrit.common import verify_and_resolve_path
from pyrit.common.path import SCORER_SCALES_PATH
from pyrit.models import MessagePiece, Score, SeedPrompt, UnvalidatedScore
from pyrit.prompt_target import PromptChatTarget
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator


class SelfAskScaleScorer(FloatScaleScorer):
    """
    A class that represents a "self-ask" score for text scoring for a customizable numeric scale.
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

    _default_validator: ScorerPromptValidator = ScorerPromptValidator(
        supported_data_types=["text"],
        is_objective_required=True,
    )

    def __init__(
        self,
        *,
        chat_target: PromptChatTarget,
        scale_arguments_path: Optional[Union[Path, str]] = None,
        system_prompt_path: Optional[Union[Path, str]] = None,
        validator: Optional[ScorerPromptValidator] = None,
    ) -> None:
        """
        Initialize the SelfAskScaleScorer.

        Args:
            chat_target (PromptChatTarget): The chat target to use for scoring.
            scale_arguments_path (Optional[Union[Path, str]]): Path to the YAML file containing scale definitions.
                Defaults to TREE_OF_ATTACKS_SCALE if not provided.
            system_prompt_path (Optional[Union[Path, str]]): Path to the YAML file containing the system prompt.
                Defaults to GENERAL_SYSTEM_PROMPT if not provided.
            validator (Optional[ScorerPromptValidator]): Custom validator for the scorer. Defaults to None.
        """
        super().__init__(validator=validator or self._default_validator)

        self._prompt_target = chat_target

        if not system_prompt_path:
            system_prompt_path = self.SystemPaths.GENERAL_SYSTEM_PROMPT.value

        if not scale_arguments_path:
            scale_arguments_path = self.ScalePaths.TREE_OF_ATTACKS_SCALE.value

        system_prompt_path = verify_and_resolve_path(system_prompt_path)
        scale_arguments_path = verify_and_resolve_path(scale_arguments_path)

        scale_args = yaml.safe_load(scale_arguments_path.read_text(encoding="utf-8"))

        self._validate_scale_arguments_set(scale_args)

        self._minimum_value = scale_args["minimum_value"]
        self._maximum_value = scale_args["maximum_value"]
        self._category = scale_args["category"]

        scoring_instructions_template = SeedPrompt.from_yaml_file(system_prompt_path)

        self._system_prompt = scoring_instructions_template.render_template_value(**scale_args)

    def _build_identifier(self) -> None:
        """Build the scorer evaluation identifier for this scorer."""
        self._set_identifier(
            system_prompt_template=self._system_prompt,
            user_prompt_template="objective: {objective}\nresponse: {response}",
            prompt_target=self._prompt_target,
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        """
        Scores the given message_piece using "self-ask" for the chat target.

        Args:
            message_piece (MessagePiece): The message piece containing the text to be scored.
            objective (str): The task based on which the text should be scored (the original
                attacker model's objective).

        Returns:
            list[Score]: The message piece's score.
                         The score_value is a value from [0,1] that is scaled based on the scorer's scale.
        """
        scoring_prompt = f"objective: {objective}\nresponse: {message_piece.converted_value}"

        unvalidated_score: UnvalidatedScore = await self._score_value_with_llm(
            prompt_target=self._prompt_target,
            system_prompt=self._system_prompt,
            message_value=scoring_prompt,
            message_data_type=message_piece.converted_value_data_type,
            scored_prompt_id=message_piece.id,
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
