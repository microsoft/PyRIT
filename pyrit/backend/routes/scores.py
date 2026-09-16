# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Score API routes."""

import asyncio
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status

from pyrit.backend.middleware.auth import AuthenticatedUser
from pyrit.backend.models.attacks import ScoreView
from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.scores import ManualScoreRequest
from pyrit.memory import CentralMemory
from pyrit.models import AttackOutcome, MessageScorable, ScoringExpectation
from pyrit.score import ManualScorer

router = APIRouter(prefix="/scores", tags=["scores"])


def _get_manual_score_outcome(*, request: ManualScoreRequest) -> AttackOutcome:
    """
    Map a manual objective verdict to an attack outcome.

    Returns:
        AttackOutcome: Success for a true verdict, otherwise failure.
    """
    return AttackOutcome.SUCCESS if request.value else AttackOutcome.FAILURE


def _get_user_identifier(*, request: Request) -> str:
    """Return the authenticated user's stable identifier."""
    user = getattr(request.state, "user", None)
    if isinstance(user, AuthenticatedUser):
        return user.email or user.oid
    return "local-development"


@router.post(
    "/manual",
    response_model=ScoreView,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"model": ProblemDetail, "description": "Message or attack not found"},
        422: {"model": ProblemDetail, "description": "Validation error"},
    },
)
async def create_manual_score(  # pyrit-async-suffix-exempt
    request_body: ManualScoreRequest,
    request: Request,
) -> ScoreView:
    """
    Create and persist a manual score for a message piece.

    Returns:
        ScoreView: The persisted manual score.
    """
    memory = CentralMemory.get_memory_instance()
    pieces = await asyncio.to_thread(memory.get_message_pieces, prompt_ids=[request_body.message_id])
    piece = next((piece for piece in pieces if str(piece.id) == str(request_body.message_id)), None)
    if piece is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message '{request_body.message_id}' not found",
        )

    attacks = await asyncio.to_thread(
        memory.get_attack_results,
        attack_result_ids=[str(request_body.attack_result_id)],
    )
    if not attacks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attack '{request_body.attack_result_id}' not found",
        )

    attack = attacks[0]
    if not piece.conversation_id or piece.conversation_id not in attack.get_active_conversation_ids():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message '{request_body.message_id}' does not belong to attack '{request_body.attack_result_id}'",
        )

    if not attack.objective.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="An attack objective is required before adding a manual score",
        )

    scorer = ManualScorer(
        value=request_body.value,
        rationale=request_body.rationale,
        user_identifier=_get_user_identifier(request=request),
    )
    scores = await scorer.score_async(
        scorable=MessageScorable(message_piece_ids=(request_body.message_id,)),
        expectation=ScoringExpectation(objective=attack.objective),
    )
    score = scores[0]
    if request_body.update_attack:
        outcome = _get_manual_score_outcome(request=request_body)
        updated = await asyncio.to_thread(
            memory.update_attack_result_by_id,
            attack_result_id=attack.attack_result_id,
            update_fields={
                "human_score_id": uuid.UUID(str(score.id)),
                "outcome": outcome,
                "outcome_reason": score.score_rationale or None,
                "timestamp": datetime.now(UTC),
            },
        )
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Attack '{attack.attack_result_id}' changed while adding the manual score",
            )

    return ScoreView.from_domain(score, is_objective_score=request_body.update_attack)
