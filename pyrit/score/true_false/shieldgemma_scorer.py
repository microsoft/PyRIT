# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import enum
from functools import partial
from typing import TYPE_CHECKING, ClassVar

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.models import ComponentIdentifier, MessagePiece, Score, SeedPrompt
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS, PromptTarget
from pyrit.score.llm_scoring import _run_llm_scoring_async
from pyrit.score.response_handler import CallableResponseHandler
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.system_prompt import _render_system_prompt_template
from pyrit.score.true_false.shieldgemma_parser import parse_shieldgemma_response
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from pyrit.score.true_false.shieldgemma_policy import ShieldGemmaGuideline

_SHIELDGEMMA_DATA_PATH = SCORER_SEED_PROMPT_PATH / "shieldgemma"
_DEFAULT_SHIELDGEMMA_PROMPT_PATH = _SHIELDGEMMA_DATA_PATH / "shieldgemma_prompt.yaml"
_PROMPT_PARAMETERS = ("subject_description", "content_label", "content", "guideline", "violation_subject")


class ShieldGemmaMessageRole(enum.Enum):
    """
    The conversation role whose message ShieldGemma classifies.

    ShieldGemma uses different phrasing depending on whether it is judging a user prompt or
    a model response, so the role selects the wording used in the request.
    """

    USER = "user"
    CHATBOT = "chatbot"

    @property
    def subject_description(self) -> str:
        """How the request describes what is being judged."""
        return "a user prompt" if self is ShieldGemmaMessageRole.USER else "an AI response to a prompt"

    @property
    def content_label(self) -> str:
        """The label prefixed to the classified message."""
        return "Human Question" if self is ShieldGemmaMessageRole.USER else "Chatbot Response"

    @property
    def violation_subject(self) -> str:
        """How the request refers to the message in the final question."""
        return "human question" if self is ShieldGemmaMessageRole.USER else "Chatbot Response"


def render_shieldgemma_prompt(
    *,
    message: str,
    guideline: ShieldGemmaGuideline,
    message_role: ShieldGemmaMessageRole = ShieldGemmaMessageRole.CHATBOT,
    prompt_template: SeedPrompt | str | None = None,
) -> SeedPrompt:
    """
    Render a ShieldGemma classification request for one message and one guideline.

    Args:
        message (str): The message to classify.
        guideline (ShieldGemmaGuideline): The single safety principle to judge against.
        message_role (ShieldGemmaMessageRole): Whether the message is a user prompt or a
            model response. Defaults to the response side.
        prompt_template (SeedPrompt | str | None): Custom request template. Defaults to the
            bundled ShieldGemma template.

    Returns:
        SeedPrompt: The rendered request prompt.
    """
    return _render_system_prompt_template(
        system_prompt_template=prompt_template,
        default_template_path=_DEFAULT_SHIELDGEMMA_PROMPT_PATH,
        render_params={
            "subject_description": message_role.subject_description,
            "content_label": message_role.content_label,
            "content": message,
            "guideline": guideline.rendered,
            "violation_subject": message_role.violation_subject,
        },
        required_parameters=_PROMPT_PARAMETERS,
    )


class ShieldGemmaScorer(TrueFalseScorer):
    """
    Classify text against one ShieldGemma safety guideline.

    ShieldGemma judges a single principle per request, so a scorer is bound to one
    guideline. Compose several with ``TrueFalseCompositeScorer`` to cover a whole policy.
    """

    SCORE_CATEGORY: ClassVar[str] = "shieldgemma"
    TARGET_REQUIREMENTS = CHAT_TARGET_REQUIREMENTS

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["text"])

    def __init__(
        self,
        *,
        chat_target: PromptTarget,
        guideline: ShieldGemmaGuideline,
        message_role: ShieldGemmaMessageRole = ShieldGemmaMessageRole.CHATBOT,
        prompt_template: SeedPrompt | str | None = None,
        validator: ScorerPromptValidator | None = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """
        Initialize the ShieldGemma scorer.

        Args:
            chat_target (PromptTarget): A target serving a ShieldGemma model.
            guideline (ShieldGemmaGuideline): The single safety principle to judge against.
                Load one from ``ShieldGemmaPolicy.default()`` or supply a custom guideline.
            message_role (ShieldGemmaMessageRole): Whether the scored message is a user
                prompt or a model response. Defaults to the response side.
            prompt_template (SeedPrompt | str | None): Custom ShieldGemma request template.
                Defaults to the bundled template.
            validator (ScorerPromptValidator | None): Custom validator. Defaults to text only.
            score_aggregator (TrueFalseAggregatorFunc): Aggregator for multi-piece scores.
                Defaults to TrueFalseScoreAggregator.OR.
        """
        self._prompt_target = chat_target
        self._guideline = guideline
        self._message_role = message_role
        self._prompt_template = _resolve_prompt_template(prompt_template=prompt_template)
        self._response_handler = CallableResponseHandler(
            parser=partial(parse_shieldgemma_response, guideline_name=guideline.name)
        )

        super().__init__(
            validator=validator or self._DEFAULT_VALIDATOR,
            score_aggregator=score_aggregator,
            chat_target=chat_target,
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the scorer identifier.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        return self._create_identifier(
            params={
                "message_role": self._message_role.value,
                "guideline": self._guideline.model_dump(),
                "prompt_template": self._prompt_template.value,
            },
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
            prompt_target=self._prompt_target.get_identifier(),
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        """
        Score one text message against the configured ShieldGemma guideline.

        Args:
            message_piece (MessagePiece): The text message to classify.
            objective (str | None): Objective retained on the resulting score. It is not
                included in the ShieldGemma request. Defaults to None.

        Returns:
            list[Score]: A single true/false ShieldGemma score.
        """
        request_prompt = render_shieldgemma_prompt(
            message=message_piece.converted_value,
            guideline=self._guideline,
            message_role=self._message_role,
            prompt_template=self._prompt_template,
        )
        unvalidated_score = await _run_llm_scoring_async(
            chat_target=self._prompt_target,
            system_prompt=None,
            response_handler=self._response_handler,
            value=request_prompt.value,
            data_type="text",
            scored_prompt_id=message_piece.id,
            scorer_identifier=self.get_identifier(),
            category=self.SCORE_CATEGORY,
            objective=objective,
        )
        return [
            unvalidated_score.to_score(
                score_value=unvalidated_score.raw_score_value,
                score_type="true_false",
            )
        ]


def _resolve_prompt_template(*, prompt_template: SeedPrompt | str | None) -> SeedPrompt:
    if prompt_template is None:
        return SeedPrompt.from_yaml_file(_DEFAULT_SHIELDGEMMA_PROMPT_PATH)
    if isinstance(prompt_template, SeedPrompt):
        return prompt_template
    if isinstance(prompt_template, str):
        return SeedPrompt(value=prompt_template, data_type="text", is_jinja_template=True)
    raise TypeError("prompt_template must be a SeedPrompt, str, or None.")
