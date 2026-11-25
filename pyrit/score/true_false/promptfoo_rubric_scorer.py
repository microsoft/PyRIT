# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# Grading prompts ported from promptfoo (https://github.com/promptfoo/promptfoo), Copyright (c) Promptfoo,
# MIT License. Source revision: 825929e64f3eec83e376df98f0b3abad5e7ccee6.
# See THIRD_PARTY_NOTICES.txt for attribution and source details.

"""Grade a response against a rubric, using promptfoo's llm-rubric grading prompts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.exceptions import InvalidJsonException
from pyrit.models import SatisfiesRubric, ScoringExpectation, SeedPrompt
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS
from pyrit.score.observation.target_judge import JudgmentRequest
from pyrit.score.response_handler import JsonSchemaResponseHandler
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.self_ask_true_false_scorer import SelfAskTrueFalseScorer, TrueFalseQuestion

if TYPE_CHECKING:
    from pyrit.models import ComponentIdentifier, JsonSchemaDefinition, MessagePiece, Score
    from pyrit.prompt_target import PromptTarget


class _PromptfooGradeResponseHandler(JsonSchemaResponseHandler):
    """Read promptfoo's ``{reason, pass, score}`` grade, where a passed rubric is a True score."""

    def __init__(self, *, response_schema: JsonSchemaDefinition | None) -> None:
        super().__init__(response_schema=response_schema, rationale_output_key="reason")

    def _replay_identifier(self) -> dict[str, object]:
        """Return the base parsing contract and this grade conversion."""
        return {**super()._replay_identifier(), "promptfoo_grade_version": 2}

    def _convert_response(self, parsed_response: dict[str, Any]) -> dict[str, Any]:
        """
        Turn promptfoo's pass verdict into a true/false score, keeping its grade as metadata.

        Returns:
            dict[str, Any]: The response with a ``score_value`` that is True when the rubric passed.

        Raises:
            InvalidJsonException: If ``pass`` is not a boolean or ``reason`` is not a string.
        """
        passed = parsed_response.get("pass")
        if not isinstance(passed, bool):
            raise InvalidJsonException(message="promptfoo grade 'pass' must be a boolean.")
        if not isinstance(parsed_response.get("reason"), str):
            raise InvalidJsonException(message="promptfoo grade 'reason' must be a string.")
        metadata: dict[str, Any] = {"promptfoo_pass": str(passed).lower()}
        if isinstance(parsed_response.get("score"), (int, float)) and not isinstance(parsed_response["score"], bool):
            metadata["promptfoo_score"] = float(parsed_response["score"])
        return {
            **parsed_response,
            "score_value": passed,
            "description": "The response satisfies the rubric."
            if passed
            else "The response does not satisfy the rubric.",
            "metadata": metadata,
        }


class PromptfooRubricScorer(SelfAskTrueFalseScorer):
    """
    Grade a response against the rubric in its ``SatisfiesRubric`` condition, with promptfoo's grading prompts.

    The judge receives promptfoo's ``llm-rubric`` system prompt and user message: the response in an
    ``<Output>`` block and the rubric in a ``<Rubric>`` block. True means the response satisfies the
    rubric, which is promptfoo's ``pass`` verdict. Because the rubric comes from each objective's
    condition, one scorer grades every rubric. Wrap it in ``TrueFalseInverterScorer`` when a failed
    rubric is the attack's success, as for promptfoo's red-team plugins.

    Reference: [@promptfoo2025redteam]
    """

    CONDITION_TYPE = SatisfiesRubric
    TARGET_REQUIREMENTS = CHAT_TARGET_REQUIREMENTS
    SYSTEM_PROMPT_PATH = SCORER_SEED_PROMPT_PATH / "promptfoo" / "llm_rubric_system_prompt.yaml"
    USER_PROMPT_PATH = SCORER_SEED_PROMPT_PATH / "promptfoo" / "llm_rubric_user_prompt.yaml"
    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["text"])

    def __init__(self, *, chat_target: PromptTarget, validator: ScorerPromptValidator | None = None) -> None:
        """
        Initialize the scorer with the judge target.

        Args:
            chat_target (PromptTarget): The judge target, not the application under evaluation.
            validator (ScorerPromptValidator | None): Custom validator. Defaults to text responses only.
        """
        system_prompt = SeedPrompt.from_yaml_file(self.SYSTEM_PROMPT_PATH)
        self._user_prompt = SeedPrompt.from_yaml_file(self.USER_PROMPT_PATH)
        super().__init__(
            chat_target=chat_target,
            system_prompt=system_prompt,
            question=TrueFalseQuestion(
                category="promptfoo_rubric",
                true_description="The response satisfies the rubric.",
                false_description="The response does not satisfy the rubric.",
            ),
            response_handler=_PromptfooGradeResponseHandler(response_schema=system_prompt.response_json_schema),
            validator=validator,
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Identify the grading prompts and the judge.

        Returns:
            ComponentIdentifier: The scorer configuration.
        """
        return self._create_identifier(
            params={
                "system_prompt_template": self._system_prompt,
                "user_prompt_template": self._user_prompt.value,
                "response_json_schema": self._response_handler.json_response_config.json_schema,
            },
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:missing-attribute]
            prompt_target=self._prompt_target.get_identifier(),
        )

    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        """
        Grade one response against its condition's rubric.

        Returns:
            list[Score]: The judge's true/false score, True when the response satisfies the rubric.
        """
        condition = self._get_required_condition(expectation=expectation, condition_type=SatisfiesRubric)
        unvalidated_score = await self._judge.judge_async(
            response_handler=self._response_handler,
            request=self._capture_judgment_evidence(
                JudgmentRequest(
                    expectation=expectation,
                    system_prompt=self._system_prompt,
                    value=self._user_prompt.render_template_value(
                        output=message_piece.converted_value, rubric=condition.rubric
                    ),
                    data_type="text",
                    scored_prompt_id=message_piece.id,
                    scorer_identifier=self.get_identifier(),
                    judgment_replay_identifier=self._get_judgment_replay_identifier(),
                    category=self._score_category,
                )
            ),
            fresh_conversation_per_attempt=True,
        )
        return [self._convert_score(unvalidated_score)]
