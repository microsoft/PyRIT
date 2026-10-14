# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Objective-achievement probabilities from a LiteLLM decision model."""

from __future__ import annotations

import json
import math
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from pyrit.common import YamlLoadable
from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.exceptions import InvalidJsonException
from pyrit.models import ComponentIdentifier, MatchesObjective, MessagePiece, Score
from pyrit.prompt_target import PromptTarget, TargetRequirements
from pyrit.score.float_scale.float_scale_scorer import MessageFloatScaleScorer
from pyrit.score.llm_scoring import _parse_judgment_observation
from pyrit.score.observation.target_judge import JudgmentRequest, TargetJudge
from pyrit.score.response_handler import CallableResponseHandler
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator

if TYPE_CHECKING:
    from pyrit.models import Observation, ScoringExpectation, UnvalidatedScore
    from pyrit.score.observation.execution import _ObservationEvidence


class _DecisionQuestion(BaseModel, YamlLoadable):
    """The instructions and event criteria supplied to a decision model."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    category: Annotated[str, StringConstraints(strip_whitespace=True)] = Field(min_length=1)
    instructions: str = Field(min_length=1)
    true_description: str = Field(min_length=1)
    false_description: str = Field(min_length=1)

    @field_validator("instructions", "true_description", "false_description")
    @classmethod
    def _validate_prompt_text(cls, value: str) -> str:
        """
        Validate prompt text without changing the model request.

        Args:
            value (str): The instruction or criterion text.

        Returns:
            str: The unchanged text.

        Raises:
            ValueError: If the text is empty or contains only whitespace.
        """
        if not value.strip():
            raise ValueError("Decision instructions and criteria must not be blank.")
        return value


def _parse_objective_decision_response(text: str, *, question_name: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidJsonException(message="The decision response is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise InvalidJsonException(message="The decision response must contain an answers object.")
    return {
        "score_value": _read_objective_probability(payload=payload, question_name=question_name),
        "description": "Model-reported probability that the response achieves the objective.",
        "rationale": "",
        "metadata": _decision_metadata(payload),
    }


def _read_objective_probability(*, payload: dict[str, Any], question_name: str) -> int | float:
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise InvalidJsonException(message="The decision response must contain an answers object.")
    answer = answers.get(question_name)
    if not isinstance(answer, dict) or answer.get("type") != "noul":
        raise InvalidJsonException(message=f"The decision response must contain a noul answer for {question_name}.")
    probability = answer.get("noul")
    if (
        isinstance(probability, bool)
        or not isinstance(probability, (int, float))
        or not math.isfinite(probability)
        or not 0 <= probability <= 1
    ):
        raise InvalidJsonException(message="The objective-achievement probability must be a finite number in [0, 1].")
    return probability


def _decision_metadata(payload: dict[str, Any]) -> dict[str, str | int | float]:
    metadata: dict[str, str | int | float] = {}
    for key in ("model", "provider", "id"):
        if key in payload:
            value = payload[key]
            if not isinstance(value, str):
                raise InvalidJsonException(message=f"Decision response {key} must be a string.")
            metadata["response_id" if key == "id" else key] = value
    usage = payload.get("usage", {})
    if not isinstance(usage, dict):
        raise InvalidJsonException(message="Decision response usage must be an object.")
    for key in ("input_tokens", "output_tokens", "cost"):
        if key in usage:
            value = usage[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise InvalidJsonException(
                    message=f"Decision response usage.{key} must be a finite nonnegative number."
                )
            metadata[key] = value
    return metadata


class DecisionScorer(MessageFloatScaleScorer):
    """
    Return an objective-achievement probability without applying a pass/fail threshold.

    Validation applies to the target's JSON response. ``LiteLLMDecisionTarget`` returns
    SDK-normalized JSON, where Boolean answers and numeric strings are converted to numbers.
    """

    CONDITION_TYPE = MatchesObjective
    QUESTION_NAME: str = "objective_achieved"
    _DEFAULT_QUESTION_PATH: Path = Path(SCORER_SEED_PROMPT_PATH, "decision", "objective_achieved.yaml")
    RESPONSE_PARSER_FINGERPRINT: str = "decision-objective-response-v1"
    TARGET_REQUIREMENTS = TargetRequirements(
        required_input_modalities=frozenset({frozenset({"text"})}),
        required_output_modalities=frozenset({frozenset({"text"})}),
    )
    _DEFAULT_VALIDATOR = ScorerPromptValidator(supported_data_types=["text"], is_objective_required=True)

    def __init__(self, *, chat_target: PromptTarget, decision_question_path: str | Path | None = None) -> None:
        """
        Initialize the scorer with a target serving the System One decision contract.

        Args:
            chat_target (PromptTarget): Target serving the LiteLLM decision API.
            decision_question_path (str | Path | None): YAML containing the instructions,
                category, and true/false event criteria. Prompt text is preserved as written.
                Defaults to the bundled objective question.
        """
        super().__init__(validator=self._DEFAULT_VALIDATOR)
        self._prompt_target = chat_target
        self._judge = TargetJudge(target=chat_target, requirements=self.TARGET_REQUIREMENTS)
        self._question = _DecisionQuestion.from_yaml_file(
            decision_question_path if decision_question_path is not None else self._DEFAULT_QUESTION_PATH
        )
        self._response_handler = CallableResponseHandler(
            parser=partial(_parse_objective_decision_response, question_name=self.QUESTION_NAME),
            parser_fingerprint=self.RESPONSE_PARSER_FINGERPRINT,
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identity from the instructions, rubric, decision question, and target.

        Returns:
            ComponentIdentifier: The scorer identity.
        """
        return self._create_identifier(
            params={
                "question_name": self.QUESTION_NAME,
                "question": self._question.model_dump(),
            },
            prompt_target=self._prompt_target.get_identifier(),
        )

    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        """
        Prepare an objective question and retain the returned probability.

        Args:
            message_piece (MessagePiece): The text evidence to evaluate.
            expectation (ScoringExpectation | None): The objective and typed criteria.

        Returns:
            list[Score]: One objective-achievement probability.

        Raises:
            ValueError: If the objective is empty or contains only whitespace.
        """
        objective = expectation.objective if expectation else None
        if not objective or not objective.strip():
            raise ValueError("A nonempty objective is required for decision scoring.")
        unvalidated = await self._judge.judge_async(
            response_handler=self._response_handler,
            request=self._capture_judgment_evidence(
                JudgmentRequest(
                    expectation=expectation,
                    system_prompt=None,
                    value=self._build_decision_value(objective=objective, response=message_piece.converted_value),
                    data_type="text",
                    scored_prompt_id=message_piece.id,
                    scorer_identifier=self.get_identifier(),
                    judgment_replay_identifier=self._get_judgment_replay_identifier(),
                    category=self._question.category,
                )
            ),
            fresh_conversation_per_attempt=True,
        )
        return [self._convert_score(unvalidated)]

    def _build_decision_value(self, *, objective: str, response: str) -> str:
        """
        Build the objective and response question for the decision model.

        Args:
            objective (str): The objective to evaluate.
            response (str): The response text to evaluate.

        Returns:
            str: The state and questions as JSON.
        """
        return json.dumps(
            {
                "state": {
                    "objective": objective,
                    "response": response,
                },
                "questions": {
                    self.QUESTION_NAME: {
                        "type": "noul",
                        "instructions": self._question.instructions,
                        "criteria": {
                            "true": self._question.true_description,
                            "false": self._question.false_description,
                        },
                    }
                },
            }
        )

    def _judgment_replay_identifier(self) -> dict[str, object]:
        """Return the objective-probability conversion contract."""
        return {"version": 1}

    def _score_judgment_observation(
        self,
        *,
        observation: Observation,
        evidence: _ObservationEvidence,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        """
        Replay retained decision evidence without calling the provider.

        Args:
            observation (Observation): The retained judgment response.
            evidence (_ObservationEvidence): The captured evidence references.
            expectation (ScoringExpectation | None): The objective and typed criteria.

        Returns:
            list[Score]: One objective-achievement probability.
        """
        unvalidated = _parse_judgment_observation(
            observation=observation,
            evidence=evidence,
            response_handler=self._response_handler,
            scorer_identifier=self.get_identifier(),
            judgment_replay_identifier=self._get_judgment_replay_identifier(),
            expectation=expectation,
            category=self._question.category,
        )
        return [self._convert_score(unvalidated)]

    @staticmethod
    def _convert_score(unvalidated: UnvalidatedScore) -> Score:
        return unvalidated.to_score(score_value=unvalidated.raw_score_value, score_type="float_scale")
