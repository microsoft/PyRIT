# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Expectation-bound target evaluation, separate from raw evidence acquisition."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from pyrit.models import (
    ComponentIdentifier,
    MessagePiece,
    PromptDataType,
    Scorable,
    ScoringExpectation,
    UnvalidatedScore,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptTarget, TargetRequirements
from pyrit.score.llm_scoring import _run_llm_scoring_async
from pyrit.score.response_handler import ResponseHandler


@dataclass(frozen=True, kw_only=True)
class JudgmentRequest:
    """Prepared judge input with explicit criteria and captured evidence identity."""

    expectation: ScoringExpectation | None
    system_prompt: str | None
    value: str
    data_type: PromptDataType
    scored_prompt_id: str | UUID
    scorer_identifier: ComponentIdentifier
    prepended_text: str | None = None
    category: Sequence[str] | str | None = None
    observation_metadata: Mapping[str, str] | None = None
    requires_message_piece_evidence: bool = False
    judgment_replay_identifier: Mapping[str, object] | None = None
    scorable: Scorable | None = None
    scored_message_piece: MessagePiece | None = None


class TargetJudge:
    """Own the target requirements and exchange, not prompts or verdict conversion."""

    def __init__(self, *, target: PromptTarget, requirements: TargetRequirements) -> None:
        """Validate the concrete scorer's requirements and retain its target."""
        requirements.validate(target=target)
        self._target = target

    async def judge_async(
        self,
        *,
        request: JudgmentRequest,
        response_handler: ResponseHandler,
        normalizer: PromptNormalizer | None = None,
        fresh_conversation_per_attempt: bool = False,
    ) -> UnvalidatedScore:
        """
        Run the persisted, retry-aware exchange using explicit criteria.

        Returns:
            UnvalidatedScore: Parsed judgment for the scorer to convert.
        """
        return await _run_llm_scoring_async(
            chat_target=self._target,
            request=request,
            response_handler=response_handler,
            normalizer=normalizer,
            fresh_conversation_per_attempt=fresh_conversation_per_attempt,
        )
