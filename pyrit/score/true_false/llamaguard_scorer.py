# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import enum
from functools import partial
from typing import ClassVar

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.models import ComponentIdentifier, MessagePiece, Score, SeedPrompt
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS, PromptTarget
from pyrit.score.llm_scoring import _run_llm_scoring_async
from pyrit.score.response_handler import CallableResponseHandler
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.system_prompt import _render_system_prompt_template
from pyrit.score.true_false.llamaguard_parser import parse_llamaguard_response
from pyrit.score.true_false.llamaguard_policy import LlamaGuardPolicy
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

_LLAMAGUARD_DATA_PATH = SCORER_SEED_PROMPT_PATH / "llamaguard"
_DEFAULT_LLAMA_GUARD_3_POLICY_PATH = _LLAMAGUARD_DATA_PATH / "llamaguard_3_policy.yaml"
_DEFAULT_LLAMA_GUARD_3_PROMPT_PATH = _LLAMAGUARD_DATA_PATH / "llamaguard_3_prompt.yaml"
_PROMPT_PARAMETERS = ("message_role", "categories", "conversation")


class LlamaGuardMessageRole(enum.Enum):
    """The LlamaGuard conversation role whose final message is classified."""

    USER = "User"
    AGENT = "Agent"


def render_llamaguard_prompt(
    *,
    message: str,
    message_role: LlamaGuardMessageRole,
    policy: LlamaGuardPolicy,
    prompt_template: SeedPrompt | str | None = None,
) -> SeedPrompt:
    """
    Render a LlamaGuard classification request for one conversation turn.

    Args:
        message (str): The message to classify.
        message_role (LlamaGuardMessageRole): Whether the message represents a User or Agent turn.
        policy (LlamaGuardPolicy): The categories used for prompting and response validation.
        prompt_template (SeedPrompt | str | None): Custom request template. Defaults to the
            bundled Llama Guard 3 8B template.

    Returns:
        SeedPrompt: The rendered request prompt.
    """
    return _render_system_prompt_template(
        system_prompt_template=prompt_template,
        default_template_path=_DEFAULT_LLAMA_GUARD_3_PROMPT_PATH,
        render_params={
            "message_role": message_role.value,
            "categories": policy.rendered_categories,
            "conversation": f"{message_role.value}: {message}",
        },
        required_parameters=_PROMPT_PARAMETERS,
    )


class LlamaGuardScorer(TrueFalseScorer):
    """
    Classify text with a Llama Guard endpoint.

    The bundled default targets ``meta-llama/Llama-Guard-3-8B`` and its S1-S14
    taxonomy. Other variants require a matching policy.
    """

    SCORE_CATEGORY: ClassVar[str] = "llamaguard"
    TARGET_REQUIREMENTS = CHAT_TARGET_REQUIREMENTS

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["text"])

    def __init__(
        self,
        *,
        chat_target: PromptTarget,
        message_role: LlamaGuardMessageRole = LlamaGuardMessageRole.AGENT,
        policy: LlamaGuardPolicy | None = None,
        prompt_template: SeedPrompt | str | None = None,
        validator: ScorerPromptValidator | None = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """
        Initialize the LlamaGuard scorer.

        Args:
            chat_target (PromptTarget): A target serving a LlamaGuard model.
            message_role (LlamaGuardMessageRole): The role to classify. Defaults to Agent for
                model-response classification.
            policy (LlamaGuardPolicy | None): Safety policy used for prompting and category
                validation. Defaults to the bundled Llama Guard 3 8B S1-S14 policy.
            prompt_template (SeedPrompt | str | None): Custom LlamaGuard request template.
                Defaults to the bundled Llama Guard 3 8B template.
            validator (ScorerPromptValidator | None): Custom validator. Defaults to text only.
            score_aggregator (TrueFalseAggregatorFunc): Aggregator for multi-piece scores.
                Defaults to TrueFalseScoreAggregator.OR.
        """
        self._prompt_target = chat_target
        self._message_role = message_role
        self._policy = policy or LlamaGuardPolicy.from_yaml(_DEFAULT_LLAMA_GUARD_3_POLICY_PATH)
        self._prompt_template = _resolve_prompt_template(
            prompt_template=prompt_template,
            policy=self._policy,
        )
        self._response_handler = CallableResponseHandler(
            parser=partial(
                parse_llamaguard_response,
                allowed_categories=self._policy.category_codes,
            )
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
                "policy": self._policy.model_dump(),
                "prompt_template": self._prompt_template.value,
            },
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
            prompt_target=self._prompt_target.get_identifier(),
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        """
        Score one text message with LlamaGuard.

        Args:
            message_piece (MessagePiece): The text message to classify.
            objective (str | None): Objective retained on the resulting score. It is not included
                in the LlamaGuard conversation. Defaults to None.

        Returns:
            list[Score]: A single true/false LlamaGuard score.
        """
        request_prompt = render_llamaguard_prompt(
            message=message_piece.converted_value,
            message_role=self._message_role,
            policy=self._policy,
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
    policy: LlamaGuardPolicy,
) -> SeedPrompt:
    if prompt_template is None:
        resolved = SeedPrompt.from_yaml_file(_DEFAULT_LLAMA_GUARD_3_PROMPT_PATH)
    elif isinstance(prompt_template, SeedPrompt):
        resolved = prompt_template
    elif isinstance(prompt_template, str):
        resolved = SeedPrompt(value=prompt_template, data_type="text", is_jinja_template=True)
    else:
        raise TypeError("prompt_template must be a SeedPrompt, str, or None.")

    render_llamaguard_prompt(
        message="validation message",
        message_role=LlamaGuardMessageRole.AGENT,
        policy=policy,
        prompt_template=resolved,
    )
    return resolved
