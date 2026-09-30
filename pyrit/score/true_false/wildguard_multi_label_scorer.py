# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import json
from contextvars import ContextVar
from functools import partial
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.models import ComponentIdentifier, Message, MessagePiece, Score, ScoreStatus, ScoringExpectation, SeedPrompt
from pyrit.score.llm_scoring import _run_llm_scoring_async
from pyrit.score.response_handler import CallableResponseHandler
from pyrit.score.true_false.multi_label_true_false_scorer import MessageMultiLabelTrueFalseScorer
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseAggregatorFunc, TrueFalseScoreAggregator
from pyrit.score.true_false.wildguard_parser import WildGuardLabel, _parse_labels
from pyrit.score.true_false.wildguard_scorer import (
    _MISSING_USER_PROMPT_MESSAGE,
    WildGuardScorer,
    _resolve_prompt_template,
    _resolve_wildguard_user_prompt_async,
    _WildGuardMessageResolver,
    render_wildguard_prompt,
)

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget
    from pyrit.score.scorer_prompt_validator import ScorerPromptValidator


def _parse_multi_label_response(text: str, *, scope: str) -> dict[str, Any]:
    """
    Retain the full parsed judgment for normalization into independent scores.

    Returns:
        dict[str, Any]: An unvalidated value carrying all labels and the per-piece audit metadata.
    """
    values = _parse_labels(text.strip())
    return {
        "score_value": json.dumps({label.metadata_key: value for label, value in values.items()}),
        "rationale": "WildGuard returned independent judgments for the request and response.",
        "metadata": {
            **{f"wildguard_{scope}_{label.metadata_key}": value for label, value in values.items()},
            f"wildguard_{scope}_raw_output": text.strip(),
        },
    }


class WildGuardMultiLabelScorer(MessageMultiLabelTrueFalseScorer):
    """
    Preserve all three WildGuard judgments from one classifier call per text piece.

    Each judgment becomes an independently persisted score. ``N/A`` remains undetermined,
    and multi-piece responses aggregate within each label. The original
    ``WildGuardScorer(label=...)`` API retains its single-label behavior.
    """

    TARGET_REQUIREMENTS = WildGuardScorer.TARGET_REQUIREMENTS
    _RESOLVED_USER_PROMPT: ClassVar[ContextVar[str | None]] = ContextVar(
        "wildguard_multi_label_user_prompt", default=None
    )

    def __init__(
        self,
        *,
        chat_target: PromptTarget,
        user_prompt: str | None = None,
        prompt_template: SeedPrompt | str | None = None,
        validator: ScorerPromptValidator | None = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """
        Initialize the classifier and its per-label aggregation policy.

        Args:
            chat_target (PromptTarget): Target serving the WildGuard classifier.
            user_prompt (str | None): Fixed context, otherwise resolved from stored user history.
            prompt_template (SeedPrompt | str | None): Template accepted by ``WildGuardScorer``.
            validator (ScorerPromptValidator | None): Message role and modality policy.
            score_aggregator (TrueFalseAggregatorFunc): Per-label aggregation across supported pieces.
        """
        self._prompt_target = chat_target
        self._user_prompt = user_prompt
        self._prompt_template = _resolve_prompt_template(prompt_template=prompt_template)
        super().__init__(
            labels=tuple(label.metadata_key for label in WildGuardLabel),
            validator=validator or WildGuardScorer._DEFAULT_VALIDATOR,
            score_aggregator=score_aggregator,
            chat_target=chat_target,
            message_resolver=_WildGuardMessageResolver(),
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Include prompt configuration, label contract and aggregation policy.

        Returns:
            ComponentIdentifier: Identity of this classifier configuration.
        """
        return self._create_identifier(
            params={"user_prompt": self._user_prompt, "prompt_template": self._prompt_template.value},
            score_aggregator=self._score_aggregator.__name__,  # type: ignore[ty:unresolved-attribute]
            prompt_target=self._prompt_target.get_identifier(),
        )

    async def _score_async(
        self, message: Message, *, objective: str | None = None, expectation: ScoringExpectation | None = None
    ) -> list[Score]:
        """
        Resolve user context once before scoring pieces and aggregating each label.

        Returns:
            list[Score]: Three labeled aggregates, or no scores for unsupported evidence.

        Raises:
            ValueError: If no nonblank user prompt is available.
        """
        pieces = self._get_supported_pieces(message)
        if not pieces:
            return []
        user_prompt = await _resolve_wildguard_user_prompt_async(
            memory=self._memory, user_prompt=self._user_prompt, message_piece=pieces[0]
        )
        if not user_prompt:
            raise ValueError(_MISSING_USER_PROMPT_MESSAGE)
        token = self._RESOLVED_USER_PROMPT.set(user_prompt)
        try:
            return await super()._score_async(message, objective=objective, expectation=expectation)
        finally:
            self._RESOLVED_USER_PROMPT.reset(token)

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        """
        Normalize one classifier response into its independent labeled verdicts.

        Returns:
            list[Score]: Three scores sharing one retained classifier observation.

        Raises:
            ValueError: If the message's user prompt was not resolved before scoring.
        """
        user_prompt = self._RESOLVED_USER_PROMPT.get()
        if not user_prompt:
            raise ValueError(_MISSING_USER_PROMPT_MESSAGE)
        request_prompt = render_wildguard_prompt(
            response=message_piece.converted_value, user_prompt=user_prompt, prompt_template=self._prompt_template
        )
        parsed = await _run_llm_scoring_async(
            chat_target=self._prompt_target,
            system_prompt=None,
            response_handler=CallableResponseHandler(
                parser=partial(_parse_multi_label_response, scope=str(message_piece.id))
            ),
            value=request_prompt.value,
            data_type="text",
            scored_prompt_id=message_piece.id,
            scorer_identifier=self.get_identifier(),
            objective=objective,
        )
        values = json.loads(parsed.raw_score_value)
        return [
            Score(
                score_type="true_false",
                score_value=None
                if values[label.metadata_key] == "n/a"
                else str(values[label.metadata_key] == "yes").lower(),
                status=ScoreStatus.UNDETERMINED if values[label.metadata_key] == "n/a" else ScoreStatus.COMPLETE,
                score_category=[label.metadata_key],
                score_rationale=f"WildGuard answered '{label.value}: {values[label.metadata_key]}'.",
                score_metadata={**(parsed.score_metadata or {}), "selected_label": label.value},
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece.id,
                scorable=parsed.scorable,
                observation_ids=list(parsed.observation_ids),
                objective=objective,
            )
            for label in WildGuardLabel
        ]
