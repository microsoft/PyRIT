# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.models import ComponentIdentifier, Message, MessagePiece, Score, SeedPrompt
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS, PromptTarget
from pyrit.score.llm_scoring import _run_llm_scoring_async
from pyrit.score.response_handler import CallableResponseHandler
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.system_prompt import _render_system_prompt_template
from pyrit.score.true_false.shieldgemma_parser import _metadata_prefix, parse_shieldgemma_response
from pyrit.score.true_false.shieldgemma_policy import (
    ShieldGemmaGuideline,
    ShieldGemmaMessageRole,
)
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.true_false_scorer import MessageTrueFalseScorer

if TYPE_CHECKING:
    from pathlib import Path

_SHIELDGEMMA_DATA_PATH = SCORER_SEED_PROMPT_PATH / "shieldgemma"
_DEFAULT_PROMPT_ONLY_PATH = _SHIELDGEMMA_DATA_PATH / "shieldgemma_prompt.yaml"
_DEFAULT_RESPONSE_ONLY_PATH = _SHIELDGEMMA_DATA_PATH / "shieldgemma_response_prompt.yaml"

_PROMPT_ONLY_PARAMETERS = ("user_prompt", "guideline")
_RESPONSE_ONLY_PARAMETERS = ("response", "guideline")

# The verdict tokens ShieldGemma answers with, reused for the aggregated verdict so the
# metadata reads the same whether it came from the model or from the aggregator.
_VIOLATION_TOKEN = "Yes"
_COMPLIANT_TOKEN = "No"


def _coerce_message_role(message_role: ShieldGemmaMessageRole | str) -> ShieldGemmaMessageRole:
    """
    Accept the enum or its serialized value.

    ``ScorerRegistry`` inspects constructor signatures with ``inspect.signature``, which under
    postponed annotations reports the annotation as the string ``"ShieldGemmaMessageRole"``
    rather than the enum. It therefore cannot coerce a configured ``"user"``, and the raw string
    would fail every identity check and silently take the response-side path.

    Args:
        message_role (ShieldGemmaMessageRole | str): The role, or its value such as ``"user"``.

    Returns:
        ShieldGemmaMessageRole: The corresponding enum member.

    Raises:
        ValueError: If the value does not name a role.
    """
    if isinstance(message_role, ShieldGemmaMessageRole):
        return message_role
    try:
        return ShieldGemmaMessageRole(message_role.casefold())
    except ValueError:
        valid = ", ".join(role.value for role in ShieldGemmaMessageRole)
        raise ValueError(f"Unknown ShieldGemma message role {message_role!r}. Expected one of: {valid}.") from None


def _default_template_path(message_role: ShieldGemmaMessageRole) -> Path:
    if message_role is ShieldGemmaMessageRole.USER:
        return _DEFAULT_PROMPT_ONLY_PATH
    return _DEFAULT_RESPONSE_ONLY_PATH


def render_shieldgemma_prompt(
    *,
    message: str,
    guideline: ShieldGemmaGuideline,
    message_role: ShieldGemmaMessageRole | str = ShieldGemmaMessageRole.CHATBOT,
    prompt_template: SeedPrompt | str | None = None,
) -> SeedPrompt:
    """
    Render a ShieldGemma classification request for one message and one guideline.

    Prompt classification judges a user turn. Response classification judges a model turn
    without including the originating prompt, so unrelated or harmful prompt content cannot
    influence the response verdict.

    Args:
        message (str): The message to classify. This is the user prompt for
            ``ShieldGemmaMessageRole.USER`` and the model response for
            ``ShieldGemmaMessageRole.CHATBOT``.
        guideline (ShieldGemmaGuideline): The single safety principle to judge against.
        message_role (ShieldGemmaMessageRole | str): Which use case to render, as the enum or
            its value such as ``"user"``. Defaults to the response side.
        prompt_template (SeedPrompt | str | None): Custom request template. Defaults to the
            bundled template for the selected use case.

    Returns:
        SeedPrompt: The rendered request prompt.

    Raises:
        ValueError: If ``message_role`` does not name a role.
    """
    message_role = _coerce_message_role(message_role)
    if message_role is ShieldGemmaMessageRole.USER:
        render_params = {"user_prompt": message, "guideline": guideline.rendered(message_role)}
        required_parameters: tuple[str, ...] = _PROMPT_ONLY_PARAMETERS
    else:
        render_params = {
            "response": message,
            "guideline": guideline.rendered(message_role),
        }
        required_parameters = _RESPONSE_ONLY_PARAMETERS

    return _render_system_prompt_template(
        system_prompt_template=prompt_template,
        default_template_path=_default_template_path(message_role),
        render_params=render_params,
        required_parameters=required_parameters,
    )


class ShieldGemmaScorer(MessageTrueFalseScorer):
    """
    Classify text against one ShieldGemma safety guideline.

    ShieldGemma judges a single principle per request, so a scorer is bound to one
    guideline. Compose several with ``TrueFalseCompositeScorer`` to cover a whole policy.

    The default configuration classifies a model response on its own. To classify a user
    prompt, use ``ShieldGemmaMessageRole.USER``.

    Reference: [@zeng2024shieldgemma]
    Paper: https://arxiv.org/abs/2407.21772
    """

    SCORE_CATEGORY: ClassVar[str] = "shieldgemma"
    TARGET_REQUIREMENTS = CHAT_TARGET_REQUIREMENTS

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["text"])

    def __init__(
        self,
        *,
        chat_target: PromptTarget,
        guideline: ShieldGemmaGuideline,
        message_role: ShieldGemmaMessageRole | str = ShieldGemmaMessageRole.CHATBOT,
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
            message_role (ShieldGemmaMessageRole | str): Whether the scored message is a user
                prompt or a model response, as the enum or its value such as ``"user"``, which
                is what a serialized configuration supplies. Defaults to the response side.
            prompt_template (SeedPrompt | str | None): Custom ShieldGemma request template.
                Defaults to the bundled template for the selected use case.
            validator (ScorerPromptValidator | None): Custom validator. Defaults to text only.
            score_aggregator (TrueFalseAggregatorFunc): Aggregator for multi-piece scores.
                Defaults to TrueFalseScoreAggregator.OR.

        Raises:
            ValueError: If ``message_role`` does not name a role.
        """
        message_role = _coerce_message_role(message_role)

        self._prompt_target = chat_target
        self._guideline = guideline
        self._message_role = message_role
        self._prompt_template = _resolve_prompt_template(
            prompt_template=prompt_template,
            guideline=guideline,
            message_role=message_role,
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

        return self._create_identifier(
            params=params,
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
            response_handler=CallableResponseHandler(
                parser=partial(
                    parse_shieldgemma_response,
                    guideline_name=self._guideline.name,
                    scope=str(message_piece.id),
                )
            ),
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

    async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
        """
        Score every supported piece and record the aggregated verdict.

        Each piece keeps its own verdict and raw output under its own keys, so none is lost to
        the last-writer-wins metadata merge. This adds the guideline-level verdict on top, which
        follows the configured aggregator rather than whichever piece happened to be merged
        last.

        Args:
            message (Message): The message to score.
            objective (str | None): Objective retained on the resulting score. Defaults to None.

        Returns:
            list[Score]: A single aggregated true/false score, or an empty list when no piece
                could be scored.
        """
        scores = await super()._score_async(message, objective=objective)

        if not scores:
            return scores

        aggregate = scores[0]
        prefix = _metadata_prefix(guideline_name=self._guideline.name)
        aggregate.score_metadata = {
            **(aggregate.score_metadata or {}),
            f"{prefix}_verdict": _VIOLATION_TOKEN if aggregate.get_value() else _COMPLIANT_TOKEN,
        }
        return scores


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
        prompt_template=resolved,
    )
    return resolved
