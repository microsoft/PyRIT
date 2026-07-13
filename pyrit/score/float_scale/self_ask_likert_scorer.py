# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import enum
from pathlib import Path
from typing import TYPE_CHECKING

from pyrit.common.path import HARM_DEFINITION_PATH, SCORER_LIKERT_PATH
from pyrit.exceptions import InvalidJsonException
from pyrit.models import (
    ComponentIdentifier,
    JsonSchemaDefinition,
    MessagePiece,
    Score,
    SeedPrompt,
    UnvalidatedScore,
)
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS, PromptTarget
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.float_scale.likert_scale import LikertScale, LikertScaleEvalFiles
from pyrit.score.llm_scoring import _run_llm_scoring_async
from pyrit.score.response_handler import JsonSchemaResponseHandler, ResponseHandler
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.system_prompt import _render_system_prompt_template

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

_DEFAULT_LIKERT_SYSTEM_PROMPT_PATH = SCORER_LIKERT_PATH / "likert_system_prompt.yaml"


def render_likert_system_prompt(
    *,
    likert_scale: LikertScale,
    system_prompt_template: SeedPrompt | str | None = None,
) -> SeedPrompt:
    """
    Render a Likert scoring system prompt from a ``LikertScale``.

    The bundled Likert template is used when ``system_prompt_template`` is omitted. A supplied
    ``SeedPrompt`` preserves metadata such as ``response_json_schema``; a string is treated as an
    inline Jinja template.

    Args:
        likert_scale (LikertScale): The Likert scale supplying prompt parameters and bounds.
        system_prompt_template (SeedPrompt | str | None): A custom template or the bundled default.

    Returns:
        SeedPrompt: A rendered copy of the template.
    """
    return _render_system_prompt_template(
        system_prompt_template=system_prompt_template,
        default_template_path=_DEFAULT_LIKERT_SYSTEM_PROMPT_PATH,
        render_params=likert_scale.render_params,
        required_parameters=["category", "likert_scale", "min_scale_value", "max_scale_value"],
    )


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
    AI_SUPPLY_CHAIN_SCALE = (
        Path(HARM_DEFINITION_PATH, "ai_supply_chain.yaml").resolve(),
        None,
    )
    AI_SYSTEM_TRANSPARENCY_SCALE = (
        Path(HARM_DEFINITION_PATH, "ai_system_transparency.yaml").resolve(),
        None,
    )
    AI_GOVERNANCE_FAILURE_SCALE = (
        Path(HARM_DEFINITION_PATH, "ai_governance_failure.yaml").resolve(),
        None,
    )

    @property
    def path(self) -> Path:
        """The path to the Likert scale YAML file."""
        return self.value[0]

    @property
    def evaluation_files(self) -> LikertScaleEvalFiles | None:
        """The evaluation file configuration, or None if no evaluation dataset exists."""
        return self.value[1]

    def load(self) -> LikertScale:
        """
        Load this preset as a ``LikertScale`` with its evaluation metadata.

        Returns:
            LikertScale: The loaded scale.
        """
        return LikertScale.from_yaml(self.path, evaluation_files=self.evaluation_files)


class _LikertScaleResponseHandler(ResponseHandler):
    """Validate a parsed numeric score against the configured Likert entries."""

    def __init__(
        self,
        *,
        response_handler: ResponseHandler,
        likert_scale: LikertScale,
    ) -> None:
        self._response_handler = response_handler
        self._score_values = frozenset(entry.score_value for entry in likert_scale.entries)

    @property
    def response_format(self) -> str | None:
        """The wrapped handler's response-format hint."""
        return self._response_handler.response_format

    @property
    def response_schema(self) -> JsonSchemaDefinition | None:
        """The wrapped handler's response schema."""
        return self._response_handler.response_schema

    def parse(
        self,
        *,
        response_text: str,
        scorer_identifier: ComponentIdentifier,
        scored_prompt_id: str | uuid.UUID,
        category: Sequence[str] | str | None = None,
        objective: str | None = None,
    ) -> UnvalidatedScore:
        """
        Parse a response and require an exact configured Likert value.

        Returns:
            UnvalidatedScore: The parsed score after Likert-specific validation.

        Raises:
            InvalidJsonException: If the score is not an integer configured by the scale.
        """
        score = self._response_handler.parse(
            response_text=response_text,
            scorer_identifier=scorer_identifier,
            scored_prompt_id=scored_prompt_id,
            category=category,
            objective=objective,
        )
        try:
            numeric_value = float(score.raw_score_value)
        except ValueError:
            raise InvalidJsonException(message="Likert score_value must be numeric.") from None
        if not numeric_value.is_integer() or int(numeric_value) not in self._score_values:
            raise InvalidJsonException(
                message="Likert score_value must exactly match one of the configured scale entries."
            )
        return score


class SelfAskLikertScorer(FloatScaleScorer):
    """
    A class that represents a "self-ask" score for text scoring based on a Likert scale.
    A Likert scale consists of ranked, ordered categories and is often on a 5 or 7 point basis,
    but you can configure a ``LikertScale`` with any set of non-negative integer score values and
    descriptions directly or by loading a YAML file.
    """

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["text"])
    TARGET_REQUIREMENTS = CHAT_TARGET_REQUIREMENTS

    def __init__(
        self,
        *,
        chat_target: PromptTarget | None = None,
        system_prompt: SeedPrompt | str,
        likert_scale: LikertScale,
        response_handler: ResponseHandler | None = None,
        validator: ScorerPromptValidator | None = None,
    ) -> None:
        """
        Initialize the SelfAskLikertScorer.

        Args:
            chat_target (PromptTarget | None): The chat target used for scoring. Must satisfy
                CHAT_TARGET_REQUIREMENTS.
            system_prompt (SeedPrompt | str): The rendered or static scoring system prompt.
            likert_scale (LikertScale): The scale defining entries, category, and normalization.
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
        self._likert_scale = likert_scale

        rendered_value, schema = self._resolve_system_prompt(system_prompt)
        self._system_prompt = rendered_value
        # When the caller does not supply a response handler, the default JSON handler carries the
        # schema (if any) declared by the system prompt and enforces the numeric score contract, so
        # the round-trip forwards the schema to the scoring target. A caller-supplied handler owns
        # its own response contract.
        self._response_handler = _LikertScaleResponseHandler(
            response_handler=response_handler or JsonSchemaResponseHandler(response_schema=schema, numeric_value=True),
            likert_scale=likert_scale,
        )

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

    @classmethod
    def from_likert_scale(
        cls,
        *,
        chat_target: PromptTarget,
        likert_scale: LikertScale,
        system_prompt_template: SeedPrompt | str | None = None,
        response_handler: ResponseHandler | None = None,
        validator: ScorerPromptValidator | None = None,
    ) -> SelfAskLikertScorer:
        """
        Build a scorer whose system prompt, category and min/max are driven by a Likert scale.

        Renders the Likert scoring system prompt from ``likert_scale`` and stores that same object
        for category and score normalization.

        Args:
            chat_target (PromptTarget): The chat target used for scoring.
            likert_scale (LikertScale): The Likert scale to use.
            system_prompt_template (SeedPrompt | str | None): A custom Jinja template or the bundled
                Likert template.
            response_handler (ResponseHandler | None): Parser for the target's raw output. Defaults
                to None (uses ``JsonSchemaResponseHandler``).
            validator (ScorerPromptValidator | None): Custom validator. Defaults to None.

        Returns:
            SelfAskLikertScorer: The constructed scorer.
        """
        rendered = render_likert_system_prompt(
            likert_scale=likert_scale,
            system_prompt_template=system_prompt_template,
        )
        return cls(
            chat_target=chat_target,
            system_prompt=rendered,
            likert_scale=likert_scale,
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
                "likert_scale": self._likert_scale.model_dump(),
                "response_json_schema": self._response_handler.response_schema,
            },
            prompt_target=self._prompt_target.get_identifier(),
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        """
        Score the given message_piece using "self-ask" for the chat target.

        Args:
            message_piece (MessagePiece): The message piece containing the text to be scored.
            objective (str | None): The objective for scoring context. Currently not supported for this scorer.
                Defaults to None.

        Returns:
            list[Score]: The message_piece scored. The category is configured from the likert_scale.
                The score_value is a value from [0,1] that is scaled from the likert scale.
        """
        unvalidated_score = await _run_llm_scoring_async(
            chat_target=self._prompt_target,
            system_prompt=self._system_prompt,
            response_handler=self._response_handler,
            value=message_piece.converted_value,
            data_type=message_piece.converted_value_data_type,
            scored_prompt_id=message_piece.id,
            scorer_identifier=self.get_identifier(),
            category=self._likert_scale.category,
            objective=objective,
        )

        score = unvalidated_score.to_score(
            score_value=str(
                self.scale_value_float(
                    float(unvalidated_score.raw_score_value),
                    self._likert_scale.minimum_value,
                    self._likert_scale.maximum_value,
                )
            ),
            score_type="float_scale",
        )

        score.score_metadata = {"likert_value": int(float(unvalidated_score.raw_score_value))}

        return [score]
