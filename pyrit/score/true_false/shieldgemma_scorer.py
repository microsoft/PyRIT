# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.models import ComponentIdentifier, MessagePiece, Score, SeedPrompt
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS, PromptTarget
from pyrit.score.llm_scoring import _run_llm_scoring_async
from pyrit.score.response_handler import CallableResponseHandler
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.system_prompt import _render_system_prompt_template
from pyrit.score.true_false.shieldgemma_parser import parse_shieldgemma_response
from pyrit.score.true_false.shieldgemma_policy import (
    ShieldGemmaGuideline,
    ShieldGemmaMessageRole,
)
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from pathlib import Path

_SHIELDGEMMA_DATA_PATH = SCORER_SEED_PROMPT_PATH / "shieldgemma"
_DEFAULT_PROMPT_ONLY_PATH = _SHIELDGEMMA_DATA_PATH / "shieldgemma_prompt.yaml"
_DEFAULT_PROMPT_RESPONSE_PATH = _SHIELDGEMMA_DATA_PATH / "shieldgemma_response_prompt.yaml"

_PROMPT_ONLY_PARAMETERS = ("user_prompt", "guideline")
_PROMPT_RESPONSE_PARAMETERS = ("user_prompt", "response", "guideline")

_MISSING_USER_PROMPT_MESSAGE = (
    "ShieldGemma response classification needs the user prompt that produced the response, "
    "because the model is trained on a Human Question followed by a Chatbot Response. "
    "Score a piece that follows a user turn in a stored conversation, pass user_prompt= to "
    "the scorer, or use ShieldGemmaMessageRole.USER to classify a prompt on its own."
)


def _default_template_path(message_role: ShieldGemmaMessageRole) -> Path:
    if message_role is ShieldGemmaMessageRole.USER:
        return _DEFAULT_PROMPT_ONLY_PATH
    return _DEFAULT_PROMPT_RESPONSE_PATH


def render_shieldgemma_prompt(
    *,
    message: str,
    guideline: ShieldGemmaGuideline,
    message_role: ShieldGemmaMessageRole = ShieldGemmaMessageRole.CHATBOT,
    user_prompt: str | None = None,
    prompt_template: SeedPrompt | str | None = None,
) -> SeedPrompt:
    """
    Render a ShieldGemma classification request for one message and one guideline.

    Google documents two use cases with different instructions. Prompt-only classification
    judges a user turn on its own. Prompt-response classification judges a model turn and
    includes the user turn that produced it, because the model was trained to read both.

    Args:
        message (str): The message to classify. This is the user prompt for
            ``ShieldGemmaMessageRole.USER`` and the model response for
            ``ShieldGemmaMessageRole.CHATBOT``.
        guideline (ShieldGemmaGuideline): The single safety principle to judge against.
        message_role (ShieldGemmaMessageRole): Which use case to render. Defaults to the
            response side.
        user_prompt (str | None): The user prompt that produced ``message``. Required for
            the response side and ignored for the prompt side. Defaults to None.
        prompt_template (SeedPrompt | str | None): Custom request template. Defaults to the
            bundled template for the selected use case.

    Returns:
        SeedPrompt: The rendered request prompt.

    Raises:
        ValueError: If the response side is rendered without a user prompt.
    """
    if message_role is ShieldGemmaMessageRole.USER:
        render_params = {"user_prompt": message, "guideline": guideline.rendered(message_role)}
        required_parameters: tuple[str, ...] = _PROMPT_ONLY_PARAMETERS
    else:
        if not user_prompt:
            raise ValueError(_MISSING_USER_PROMPT_MESSAGE)
        render_params = {
            "user_prompt": user_prompt,
            "response": message,
            "guideline": guideline.rendered(message_role),
        }
        required_parameters = _PROMPT_RESPONSE_PARAMETERS

    return _render_system_prompt_template(
        system_prompt_template=prompt_template,
        default_template_path=_default_template_path(message_role),
        render_params=render_params,
        required_parameters=required_parameters,
    )


class ShieldGemmaScorer(TrueFalseScorer):
    """
    Classify text against one ShieldGemma safety guideline.

    ShieldGemma judges a single principle per request, so a scorer is bound to one
    guideline. Compose several with ``TrueFalseCompositeScorer`` to cover a whole policy.

    The default configuration classifies a model response, which ShieldGemma does in
    reference to the user prompt that produced it. That prompt is read from the preceding
    turn of the scored conversation, or can be supplied with ``user_prompt``. To classify a
    prompt on its own, use ``ShieldGemmaMessageRole.USER``.
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
        user_prompt: str | None = None,
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
            user_prompt (str | None): Fixed user prompt to classify responses against. Only
                used on the response side, where it takes precedence over the preceding
                turn of the scored conversation. Defaults to None.
            prompt_template (SeedPrompt | str | None): Custom ShieldGemma request template.
                Defaults to the bundled template for the selected use case.
            validator (ScorerPromptValidator | None): Custom validator. Defaults to text only.
            score_aggregator (TrueFalseAggregatorFunc): Aggregator for multi-piece scores.
                Defaults to TrueFalseScoreAggregator.OR.
        """
        self._prompt_target = chat_target
        self._guideline = guideline
        self._message_role = message_role
        self._user_prompt = user_prompt
        self._prompt_template = _resolve_prompt_template(
            prompt_template=prompt_template,
            guideline=guideline,
            message_role=message_role,
        )
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
        params: dict[str, Any] = {
            "message_role": self._message_role.value,
            "guideline": self._guideline.model_dump(),
            "prompt_template": self._prompt_template.value,
        }
        # A fixed user prompt changes the request that gets sent, so it belongs in the
        # identity. It is only read on the response side, so it only distinguishes there.
        if self._message_role is ShieldGemmaMessageRole.CHATBOT:
            params["user_prompt"] = self._user_prompt

        return self._create_identifier(
            params=params,
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
            prompt_target=self._prompt_target.get_identifier(),
        )

    def _resolve_user_prompt(self, message_piece: MessagePiece) -> str | None:
        """
        Find the user prompt that a scored response is judged against.

        Args:
            message_piece (MessagePiece): The response being scored.

        Returns:
            str | None: The configured prompt, otherwise the preceding user turn of the
                scored conversation, otherwise None.
        """
        if self._user_prompt:
            return self._user_prompt
        if not message_piece.conversation_id or message_piece.sequence < 1:
            return None

        conversation = self._memory.get_message_pieces(conversation_id=message_piece.conversation_id)
        # The converted value is what the target actually received. After a converter runs,
        # the original value can be the seed prompt instead, which would have ShieldGemma
        # judge the response against context the target never saw.
        preceding_turn = [
            piece.converted_value
            for piece in conversation
            if piece.sequence == message_piece.sequence - 1
            and piece.converted_value_data_type == "text"
            and piece.api_role == "user"
        ]
        return "\n".join(preceding_turn) or None

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        """
        Score one text message against the configured ShieldGemma guideline.

        Args:
            message_piece (MessagePiece): The text message to classify.
            objective (str | None): Objective retained on the resulting score. It is not
                included in the ShieldGemma request. Defaults to None.

        Returns:
            list[Score]: A single true/false ShieldGemma score.

        Raises:
            ValueError: If a response is scored and no user prompt can be found.
        """
        user_prompt = (
            self._resolve_user_prompt(message_piece) if self._message_role is ShieldGemmaMessageRole.CHATBOT else None
        )
        request_prompt = render_shieldgemma_prompt(
            message=message_piece.converted_value,
            guideline=self._guideline,
            message_role=self._message_role,
            user_prompt=user_prompt,
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


def _resolve_prompt_template(
    *,
    prompt_template: SeedPrompt | str | None,
    guideline: ShieldGemmaGuideline,
    message_role: ShieldGemmaMessageRole,
) -> SeedPrompt:
    if prompt_template is None:
        resolved = SeedPrompt.from_yaml_file(_default_template_path(message_role))
    elif isinstance(prompt_template, SeedPrompt):
        resolved = prompt_template
    elif isinstance(prompt_template, str):
        resolved = SeedPrompt(value=prompt_template, data_type="text", is_jinja_template=True)
    else:
        raise TypeError("prompt_template must be a SeedPrompt, str, or None.")

    # Render once here so a template missing a parameter fails at construction rather than
    # on the first scored message.
    render_shieldgemma_prompt(
        message="validation message",
        guideline=guideline,
        message_role=message_role,
        user_prompt="validation prompt",
        prompt_template=resolved,
    )
    return resolved
