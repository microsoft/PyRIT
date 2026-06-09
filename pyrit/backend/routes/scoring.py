# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
On-demand scoring routes.

Surfaces two related endpoints:

* ``GET /scorers`` — minimal list of registered scorer instances for the GUI dropdown.
* ``POST /attacks/{attack_result_id}/conversations/{conversation_id}/scores`` — score
  either the last assistant message in a conversation or the whole conversation
  (the latter wraps the chosen scorer in a ``ConversationScorer``).
* ``POST /attacks/{attack_result_id}/conversations/{conversation_id}/pieces/{piece_id}/scores``
  — score a single message piece.

All scoring is delegated to ``ScoringService``, which itself calls ``Scorer.score_async``
so the resulting scores are persisted in PyRIT memory and surfaced automatically by
``GET /attacks/{id}/messages`` on the next refresh.
"""

import logging

from fastapi import APIRouter, HTTPException, status

from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.scoring import (
    ScoreConversationRequest,
    ScoreMessageRequest,
    ScoreResponse,
    ScorerListResponse,
)
from pyrit.backend.services.scoring_service import get_scoring_service

logger = logging.getLogger(__name__)

scorers_router = APIRouter(prefix="/scorers", tags=["scorers"])
attack_scoring_router = APIRouter(prefix="/attacks", tags=["attacks"])


@scorers_router.get(
    "",
    response_model=ScorerListResponse,
)
async def list_scorers() -> ScorerListResponse:  # pyrit-async-suffix-exempt
    """
    List every registered scorer instance.

    Returns:
        ScorerListResponse: Registered scorers in registry-name order.
    """
    service = get_scoring_service()
    return await service.list_scorers_async()


@attack_scoring_router.post(
    "/{attack_result_id}/conversations/{conversation_id}/scores",
    response_model=ScoreResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ProblemDetail, "description": "Invalid scoring request"},
        404: {"model": ProblemDetail, "description": "Attack, conversation, or scorer not found"},
    },
)
async def score_conversation(  # pyrit-async-suffix-exempt
    attack_result_id: str,
    conversation_id: str,
    request: ScoreConversationRequest,
) -> ScoreResponse:
    """
    Score a conversation belonging to an attack with a registered scorer.

    Args:
        attack_result_id (str): The AttackResult primary key.
        conversation_id (str): The conversation to score (must belong to the attack).
        request (ScoreConversationRequest): Scorer name, mode, and optional objective.

    Returns:
        ScoreResponse: The scores produced by the scorer (also persisted to memory).
    """
    service = get_scoring_service()

    try:
        return await service.score_conversation_async(
            attack_result_id=attack_result_id,
            conversation_id=conversation_id,
            request=request,
        )
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        logger.exception(
            "Failed to score conversation '%s' on attack '%s'", conversation_id, attack_result_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error. Check server logs for details.",
        ) from e


@attack_scoring_router.post(
    "/{attack_result_id}/conversations/{conversation_id}/pieces/{piece_id}/scores",
    response_model=ScoreResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ProblemDetail, "description": "Invalid scoring request"},
        404: {"model": ProblemDetail, "description": "Attack, conversation, piece, or scorer not found"},
    },
)
async def score_message_piece(  # pyrit-async-suffix-exempt
    attack_result_id: str,
    conversation_id: str,
    piece_id: str,
    request: ScoreMessageRequest,
) -> ScoreResponse:
    """
    Score a single message piece with a registered scorer.

    Args:
        attack_result_id (str): The AttackResult primary key.
        conversation_id (str): The conversation containing the piece.
        piece_id (str): The message-piece id to score.
        request (ScoreMessageRequest): Scorer name and optional objective.

    Returns:
        ScoreResponse: The scores produced by the scorer (also persisted to memory).
    """
    service = get_scoring_service()

    try:
        return await service.score_message_async(
            attack_result_id=attack_result_id,
            conversation_id=conversation_id,
            piece_id=piece_id,
            request=request,
        )
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        logger.exception(
            "Failed to score piece '%s' on conversation '%s' (attack '%s')",
            piece_id,
            conversation_id,
            attack_result_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error. Check server logs for details.",
        ) from e
