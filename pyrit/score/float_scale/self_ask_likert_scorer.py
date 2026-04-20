# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import enum
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from pyrit.common.path import HARM_DEFINITION_PATH, SCORER_LIKERT_PATH
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

    human_labeled_datasets_files: List[str]
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
            human_labeled_datasets_files=["harm/exploit.csv"],
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
        None,
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
    A class that represents a "self-ask" score for text scoring for a likert scale.
    """

    _default_validator: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["text"])

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
        super().__init__(validator=validator or self._default_validator)

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

    def _build_identifier(self) -> None:
        """Build the scorer evaluation identifier for this scorer."""
        self._set_identifier(
            system_prompt_template=self._system_prompt,
            prompt_target=self._prompt_target,
        )

    def _set_likert_scale_system_prompt(self, likert_scale_path: Path) -> None:
        """
        Set the Likert scale to use for scoring.

        Args:
            likert_scale_path (Path): The path to the YAML file containing the Likert scale description.

        Raises:
            ValueError: If the Likert scale YAML file is improperly formatted.
        """
        likert_scale = yaml.safe_load(likert_scale_path.read_text(encoding="utf-8"))

        if likert_scale["category"]:
            self._score_category = likert_scale["category"]
        else:
            raise ValueError(f"Improperly formatted likert scale yaml file. Missing category in {likert_scale_path}.")

        likert_scale_str = self._likert_scale_description_to_string(likert_scale["scale_descriptions"])

        self._scoring_instructions_template = SeedPrompt.from_yaml_file(
            SCORER_LIKERT_PATH / "likert_system_prompt.yaml"
        )

        self._system_prompt = self._scoring_instructions_template.render_template_value(
            likert_scale=likert_scale_str, category=self._score_category
        )

    def _likert_scale_description_to_string(self, descriptions: list[Dict[str, str]]) -> str:
        """
        Convert the Likert scales to a string representation to be put in a system prompt.

        Args:
            descriptions: list[Dict[str, str]]: The Likert scale to use.

        Returns:
            str: The string representation of the Likert scale.

        Raises:
            ValueError: If the Likert scale YAML file is improperly formatted.
        """
        if not descriptions:
            raise ValueError("Improperly formatted Likert scale yaml file. No likert scale_descriptions provided")

        likert_scale_description = ""

        for description in descriptions:
            name = description["score_value"]
            desc = description["description"]

            if int(name) < 0 or int(name) > 5:
                raise ValueError(
                    "Improperly formatted Likert scale yaml file. Likert scale values must be between 1 and 5"
                )

            likert_scale_description += f"'{name}': {desc}\n"

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
            objective=objective,
        )

        score = unvalidated_score.to_score(
            score_value=str(self.scale_value_float(float(unvalidated_score.raw_score_value), 1, 5)),
            score_type="float_scale",
        )

        score.score_metadata = {"likert_value": int(unvalidated_score.raw_score_value)}

        return [score]
