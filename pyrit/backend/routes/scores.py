# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Score API routes."""

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status

from pyrit.backend.models.attacks import ScoreView
from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.scores import ManualScoreRequest
from pyrit.memory import CentralMemory
from pyrit.models import AttackOutcome, MessageScorable
from pyrit.score import ManualScorer, ManualTrueFalseScorer, Scorer

router = APIRouter(prefix="/scores", tags=["scores"])


def _get_manual_scorer(*, request: ManualScoreRequest) -> Scorer:
    """
    Build the scorer matching the validated manual score request.

    Returns:
        Scorer: The manual scorer for the requested score family.
    """
    value = request.value
    if request.score_type == "true_false":
        if not isinstance(value, bool):
            raise ValueError("true_false manual scores require a boolean value")
        return ManualTrueFalseScorer(value=value, rationale=request.rationale)

    if isinstance(value, bool):
        raise ValueError("float_scale manual scores require a numeric value")
    if request.success_threshold is None:
        raise ValueError("float_scale manual scores require a success threshold")
    return ManualScorer(
        value=value,
        rationale=request.rationale,
        success_threshold=request.success_threshold,
    )


def _get_manual_score_outcome(*, request: ManualScoreRequest) -> AttackOutcome:
    """
    Map a validated manual score request to an attack outcome.

    Returns:
        AttackOutcome: The outcome derived from the supplied verdict or threshold.
    """
    if request.score_type == "true_false":
        return AttackOutcome.SUCCESS if request.value is True else AttackOutcome.FAILURE
    if request.success_threshold is None:
        raise ValueError("float_scale manual scores require a success threshold")
    return AttackOutcome.SUCCESS if request.value >= request.success_threshold else AttackOutcome.FAILURE


@router.post(
    "/manual",
    response_model=ScoreView,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"model": ProblemDetail, "description": "Message not found"},
        422: {"model": ProblemDetail, "description": "Validation error"},
    },
)
async def create_manual_score(request: ManualScoreRequest) -> ScoreView:  # pyrit-async-suffix-exempt
    """
    Create and persist a manual score for a message piece.

    Returns:
        ScoreView: The persisted manual score.
    """
    memory = CentralMemory.get_memory_instance()
    pieces = await asyncio.to_thread(memory.get_message_pieces, prompt_ids=[request.message_id])
    piece = next((piece for piece in pieces if str(piece.id) == str(request.message_id)), None)
    if piece is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message '{request.message_id}' not found",
        )

    attacks = await asyncio.to_thread(
        memory.get_attack_results,
        attack_result_ids=[str(request.attack_result_id)],
    )
    if not attacks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attack '{request.attack_result_id}' not found",
        )

    attack = attacks[0]
    if not attack.objective.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="An attack objective is required before adding a manual score",
        )
    if not piece.conversation_id or not attack.includes_conversation(piece.conversation_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The message does not belong to the specified attack",
        )

    scorer = _get_manual_scorer(request=request)
    scores = await scorer.score_async(
        scorable=MessageScorable(message_piece_ids=(request.message_id,)),
    )
    score = scores[0]
    if attack.last_score is None:
        outcome = _get_manual_score_outcome(request=request)
        updated = await asyncio.to_thread(
            memory.update_attack_result_by_id,
            attack_result_id=str(request.attack_result_id),
            update_fields={
                "last_score_id": uuid.UUID(str(score.id)),
                "outcome": outcome,
                "outcome_reason": score.score_rationale or None,
                "timestamp": datetime.now(timezone.utc),
            },
        )
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Attack '{request.attack_result_id}' changed while adding the manual score",
            )

    return ScoreView.from_domain(score)
