# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import enum
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from pyrit.common.path import HARM_DEFINITION_PATH, SCORER_LIKERT_PATH
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import MessagePiece, Score, SeedPrompt, UnvalidatedScore
from pyrit.prompt_target import PromptChatTarget
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LikertScaleEvalFiles:
    """
    Configuration for evaluating a Likert scale scorer on a set of dataset files.

    Args:
        human_labeled_datasets_files (List[str]): List of glob patterns to match CSV files.
        result_file (str): Name of the result file for storing evaluation results.
        harm_category (Optional[str]): The harm category for harm scorers. Defaults to None.
            The harm definition path is derived as "{harm_category}.yaml".
    """

    human_labeled_datasets_files: list[str]
    result_file: str
    harm_category: Optional[str] = None


class LikertScalePaths(enum.Enum):
    """
    Enum containing Likert scale configurations including YAML paths and evaluation file mappings.

    Each enum value is a tuple of (yaml_path, evaluation_files) where:
    - yaml_path: Path to the YAML file containing the Likert scale definition
    - evaluation_files: Optional LikertScaleEvalFiles for scorer evaluation, or None if no dataset exists
    """

    # Scales WITH evaluation datasets
    EXPLOITS_SCALE = (
        Path(HARM_DEFINITION_PATH, "exploits.yaml").resolve(),
        LikertScaleEvalFiles(
            human_labeled_datasets_files=["harm/exploits.csv"],
            result_file="harm/exploits_metrics.jsonl",
            harm_category="exploits",
        ),
    )
    HATE_SPEECH_SCALE = (
        Path(HARM_DEFINITION_PATH, "hate_speech.yaml").resolve(),
        LikertScaleEvalFiles(
            human_labeled_datasets_files=["harm/hate_speech.csv"],
            result_file="harm/hate_speech_metrics.jsonl",
            harm_category="hate_speech",
        ),
    )
    INFORMATION_INTEGRITY_SCALE = (
        Path(HARM_DEFINITION_PATH, "information_integrity.yaml").resolve(),
        LikertScaleEvalFiles(
            human_labeled_datasets_files=["harm/info_integrity.csv"],
            result_file="harm/information_integrity_metrics.jsonl",
            harm_category="information_integrity",
        ),
    )
    PRIVACY_SCALE = (
        Path(HARM_DEFINITION_PATH, "privacy.yaml").resolve(),
        LikertScaleEvalFiles(
            human_labeled_datasets_files=["harm/privacy.csv"],
            result_file="harm/privacy_metrics.jsonl",
            harm_category="privacy",
        ),
    )
    SELF_HARM_SCALE = (
        Path(HARM_DEFINITION_PATH, "self_harm.yaml").resolve(),
        LikertScaleEvalFiles(
            human_labeled_datasets_files=["harm/self_harm.csv"],
            result_file="harm/self_harm_metrics.jsonl",
            harm_category="self_harm",
        ),
    )
    SEXUAL_SCALE = (
        Path(HARM_DEFINITION_PATH, "sexual.yaml").resolve(),
        LikertScaleEvalFiles(
            human_labeled_datasets_files=["harm/sexual.csv"],
            result_file="harm/sexual_metrics.jsonl",
            harm_category="sexual",
        ),
    )
    VIOLENCE_SCALE = (
        Path(HARM_DEFINITION_PATH, "violence.yaml").resolve(),
        LikertScaleEvalFiles(
            human_labeled_datasets_files=["harm/violence.csv"],
            result_file="harm/violence_metrics.jsonl",
            harm_category="violence",
        ),
    )

    # Scales WITHOUT evaluation datasets (evaluation_files = None)
    BEHAVIOR_CHANGE_SCALE = (
        Path(HARM_DEFINITION_PATH, "behavior_change.yaml").resolve(),
        None,
    )
    CYBER_SCALE = (
        Path(HARM_DEFINITION_PATH, "cyber.yaml").resolve(),
        None,
    )
    DECEPTION_SCALE = (
        Path(HARM_DEFINITION_PATH, "deception.yaml").resolve(),
        None,
    )
    FAIRNESS_BIAS_SCALE = (
        Path(HARM_DEFINITION_PATH, "fairness_bias.yaml").resolve(),
        LikertScaleEvalFiles(
            human_labeled_datasets_files=["harm/fairness_bias.csv"],
            result_file="harm/fairness_bias_metrics.jsonl",
            harm_category="fairness_bias",
        ),
    )
    HARM_SCALE = (
        Path(HARM_DEFINITION_PATH, "harm.yaml").resolve(),
        None,
    )
    MISINFORMATION_SCALE = (
        Path(HARM_DEFINITION_PATH, "misinformation.yaml").resolve(),
        None,
    )
    PERSUASION_SCALE = (
        Path(HARM_DEFINITION_PATH, "persuasion.yaml").resolve(),
        None,
    )
    PHISHING_SCALE = (
        Path(HARM_DEFINITION_PATH, "phishing.yaml").resolve(),
        None,
    )

    @property
    def path(self) -> Path:
        """Get the path to the Likert scale YAML file."""
        return self.value[0]  # type: ignore[no-any-return]

    @property
    def evaluation_files(self) -> Optional[LikertScaleEvalFiles]:
        """Get the evaluation file configuration, or None if no evaluation dataset exists."""
        return self.value[1]  # type: ignore[no-any-return]


class SelfAskLikertScorer(FloatScaleScorer):
    """
    A class that represents a "self-ask" score for text scoring based on a Likert scale.
    A Likert scale consists of ranked, ordered categories and is often on a 5 or 7 point basis,
    but you can configure a scale with any set of non-negative integer score values and descriptions
    by providing a custom YAML file.
    """

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["text"])

    def __init__(
        self,
        *,
        chat_target: PromptChatTarget,
        likert_scale: LikertScalePaths,
        validator: Optional[ScorerPromptValidator] = None,
    ) -> None:
        """
        Initialize the SelfAskLikertScorer.

        Args:
            chat_target (PromptChatTarget): The chat target to use for scoring.
            likert_scale (LikertScalePaths): The Likert scale configuration to use for scoring.
            validator (Optional[ScorerPromptValidator]): Custom validator for the scorer. Defaults to None.
        """
        super().__init__(validator=validator or self._DEFAULT_VALIDATOR)

        self._prompt_target = chat_target
        self._likert_scale = likert_scale

        # Auto-set evaluation file mapping from the LikertScalePaths enum
        if likert_scale.evaluation_files is not None:
            from pyrit.score.scorer_evaluation.scorer_evaluator import (
                ScorerEvalDatasetFiles,
            )

            eval_files = likert_scale.evaluation_files
            self.evaluation_file_mapping = ScorerEvalDatasetFiles(
                human_labeled_datasets_files=eval_files.human_labeled_datasets_files,
                result_file=eval_files.result_file,
                harm_category=eval_files.harm_category,
            )

        self._set_likert_scale_system_prompt(likert_scale_path=likert_scale.path)

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier for this scorer.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        return self._create_identifier(
            params={
                "system_prompt_template": self._system_prompt,
            },
            children={
                "prompt_target": self._prompt_target.get_identifier(),
            },
        )

    def _set_likert_scale_system_prompt(self, likert_scale_path: Path) -> None:
        """
        Set the Likert scale to use for scoring.

        Parses the YAML file to extract the category and scale descriptions, then
        derives the minimum and maximum score values from the scale entries. These
        are stored as ``_min_scale_value`` and ``_max_scale_value`` so that
        ``_score_piece_async`` can normalise the raw LLM score to [0, 1] correctly
        for any custom non-negative integer range (not just the default 1-5).

        Args:
            likert_scale_path (Path): The path to the YAML file containing the Likert scale description.

        Raises:
            ValueError: If the Likert scale YAML file is improperly formatted.
        """
        likert_scale = yaml.safe_load(likert_scale_path.read_text(encoding="utf-8"))

        # Validate top-level structure
        if not isinstance(likert_scale, dict):
            raise ValueError(
                f"Likert scale YAML file '{likert_scale_path}' must contain a YAML mapping/dictionary, "
                f"but got {type(likert_scale).__name__}."
            )

        # Validate required 'category' field
        category = likert_scale.get("category")
        if not category:
            raise ValueError(f"Likert scale YAML file '{likert_scale_path}' is missing required field 'category'.")
        self._score_category = category

        # Validate required 'scale_descriptions' field
        scale_descriptions = likert_scale.get("scale_descriptions")
        if not scale_descriptions or not isinstance(scale_descriptions, list):
            raise ValueError(
                f"Likert scale YAML file '{likert_scale_path}' is missing or has invalid 'scale_descriptions'. "
                f"Expected a non-empty list of dicts with 'score_value' and 'description' keys."
            )

        likert_scale_str = self._likert_scale_description_to_string(scale_descriptions, likert_scale_path)

        # All score values have been validated as non-negative integers in _likert_scale_description_to_string,
        # so we can safely convert to int here.
        scale_values = [int(d["score_value"]) for d in scale_descriptions]
        # Derive the min/max score values from the scale descriptions so that
        # custom ranges (e.g. 0-7) are handled automatically.
        self._min_scale_value = min(scale_values)
        self._max_scale_value = max(scale_values)

        if self._min_scale_value == self._max_scale_value:
            raise ValueError(
                f"Likert scale YAML file '{likert_scale_path}' must have at least two distinct score values, "
                f"but only a single unique value was found: {self._max_scale_value}."
            )

        self._scoring_instructions_template = SeedPrompt.from_yaml_file(
            SCORER_LIKERT_PATH / "likert_system_prompt.yaml"
        )

        self._system_prompt = self._scoring_instructions_template.render_template_value(
            likert_scale=likert_scale_str,
            category=self._score_category,
            min_scale_value=str(self._min_scale_value),
            max_scale_value=str(self._max_scale_value),
        )

    def _likert_scale_description_to_string(self, descriptions: list[dict[str, str]], likert_scale_path: Path) -> str:
        """
        Convert the Likert scales to a string representation to be put in a system prompt.

        Args:
            descriptions (list[dict[str, str]]): The Likert scale entries to convert.
            likert_scale_path (Path): Path to the source YAML file (used in error messages).

        Returns:
            str: The string representation of the Likert scale.

        Raises:
            ValueError: If the Likert scale YAML file is improperly formatted.
        """
        if not descriptions:
            raise ValueError(f"Likert scale YAML file '{likert_scale_path}' has no scale_descriptions entries.")

        likert_scale_description = ""

        for i, description in enumerate(descriptions):
            if not isinstance(description, dict):
                raise ValueError(
                    f"Likert scale YAML file '{likert_scale_path}': scale_descriptions entry {i} "
                    f"must be a dict with 'score_value' and 'description' keys, but got {type(description).__name__}."
                )

            val = description.get("score_value")
            desc = description.get("description")

            if val is None:
                raise ValueError(
                    f"Likert scale YAML file '{likert_scale_path}': scale_descriptions entry {i} "
                    f"is missing required key 'score_value'."
                )
            if desc is None:
                raise ValueError(
                    f"Likert scale YAML file '{likert_scale_path}': scale_descriptions entry {i} "
                    f"is missing required key 'description'."
                )

            try:
                score_int = int(val)
            except (ValueError, TypeError) as err:
                raise ValueError(
                    f"Likert scale YAML file '{likert_scale_path}': score_value must be a non-negative integer, "
                    f"but got '{val}' in entry {i}."
                ) from err

            if score_int < 0:
                raise ValueError(
                    f"Likert scale YAML file '{likert_scale_path}': score_value must be a non-negative integer, "
                    f"but got '{val}' in entry {i}."
                )

            likert_scale_description += f"'{val}': {desc}\n"

        return likert_scale_description

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        """
        Score the given message_piece using "self-ask" for the chat target.

        Args:
            message_piece (MessagePiece): The message piece containing the text to be scored.
            objective (Optional[str]): The objective for scoring context. Currently not supported for this scorer.
                Defaults to None.

        Returns:
            list[Score]: The message_piece scored. The category is configured from the likert_scale.
                The score_value is a value from [0,1] that is scaled from the likert scale.
        """
        unvalidated_score: UnvalidatedScore = await self._score_value_with_llm(
            prompt_target=self._prompt_target,
            system_prompt=self._system_prompt,
            message_value=message_piece.converted_value,
            message_data_type=message_piece.converted_value_data_type,
            scored_prompt_id=message_piece.id,
            category=self._score_category,
            attack_identifier=message_piece.attack_identifier,
            objective=objective,
        )

        score = unvalidated_score.to_score(
            score_value=str(
                self.scale_value_float(
                    float(unvalidated_score.raw_score_value),
                    self._min_scale_value,
                    self._max_scale_value,
                )
            ),
            score_type="float_scale",
        )

        score.score_metadata = {"likert_value": int(unvalidated_score.raw_score_value)}

        return [score]
