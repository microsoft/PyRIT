# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Attack API routes.

All interactions are modeled as "attacks" - including manual conversations.
This is the attack-centric API design.
"""

import logging
from collections.abc import Sequence
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, status

from pyrit.backend.models.attacks import (
    AddMessageRequest,
    AddMessageResponse,
    AttackConversationsResponse,
    AttackListResponse,
    AttackOptionsResponse,
    AttackSummary,
    ConversationMessagesResponse,
    ConverterOptionsResponse,
    CreateAttackRequest,
    CreateAttackResponse,
    CreateConversationRequest,
    CreateConversationResponse,
    UpdateAttackRequest,
    UpdateMainConversationRequest,
    UpdateMainConversationResponse,
)
from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.services.attack_service import get_attack_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attacks", tags=["attacks"])


def _parse_labels(label_params: Optional[list[str]]) -> Optional[dict[str, str | Sequence[str]]]:
    """
    Parse 'key:value' label query params into a dict grouping values by key.

    Repeating the same key produces OR-within-key semantics downstream
    (e.g. ?label=operator:alice&label=operator:bob matches either operator).
    Different keys are combined with AND.

    Returns:
        Dict mapping each label key to a list of values, or None if no valid labels.
    """
    if not label_params:
        return None
    labels: dict[str, list[str]] = {}
    for param in label_params:
        if ":" in param:
            key, value = param.split(":", 1)
            labels.setdefault(key.strip(), []).append(value.strip())
    if not labels:
        return None
    # Widen value type to match the service signature (dict values are invariant).
    widened: dict[str, str | Sequence[str]] = dict(labels)
    return widened


@router.get(
    "",
    response_model=AttackListResponse,
)
async def list_attacks(
    attack_types: Optional[list[str]] = Query(
        None,
        description="Filter by attack type names. May be specified multiple times to OR-match "
        "across types (e.g. ?attack_types=A&attack_types=B). Case-insensitive. "
        "Omit to return all attacks regardless of type.",
    ),
    converter_types: Optional[list[str]] = Query(
        None,
        description="Filter by converter type names. May be specified multiple times; "
        "combination semantics are controlled by converter_types_match "
        "(e.g. ?converter_types=A&converter_types=B). "
        "Omit (or pass an empty value) to apply no converter filter. "
        "To restrict to attacks with no converters, use has_converters=false.",
    ),
    converter_types_match: Literal["any", "all"] = Query(
        "all",
        description="How to combine multiple converter_types: 'any' (attack has at least one) "
        "or 'all' (attack has every one). Defaults to 'all'.",
    ),
    has_converters: Optional[bool] = Query(
        None,
        description="Filter by converter presence. true = attacks with at least one converter; "
        "false = attacks with no converters. Omit for no filter.",
    ),
    outcome: Optional[Literal["undetermined", "success", "failure"]] = Query(None, description="Filter by outcome"),
    label: Optional[list[str]] = Query(
        None,
        description="Filter by labels (format: key:value). May be specified multiple times; "
        "OR-matched within a key, AND-matched across keys "
        "(e.g. ?label=op:red&label=op:blue matches op=red OR op=blue).",
    ),
    min_turns: Optional[int] = Query(None, ge=0, description="Filter by minimum executed turns"),
    max_turns: Optional[int] = Query(None, ge=0, description="Filter by maximum executed turns"),
    limit: int = Query(20, ge=1, le=100, description="Maximum items per page"),
    cursor: Optional[str] = Query(
        None,
        description="Pagination cursor: the attack_result_id of the last item from the previous page. "
        "Omit to start from the beginning. The response includes next_cursor for the next page.",
    ),
) -> AttackListResponse:
    """
    List attacks with optional filtering and pagination.

    Returns attack summaries (not full message content).
    Use GET /attacks/{id} for full details.

    Returns:
        AttackListResponse: Paginated list of attack summaries.
    """
    service = get_attack_service()
    labels = _parse_labels(label)
    # Strip empty strings from the list-valued query params. The service layer
    # coerces an all-empty ``converter_types`` list to None ("no filter"); the
    # "attacks with no converters" case is expressed through ``has_converters``.
    if converter_types is not None:
        converter_types = [c for c in converter_types if c]
    if attack_types is not None:
        attack_types = [a for a in attack_types if a]
    return await service.list_attacks_async(
        attack_types=attack_types,
        converter_types=converter_types,
        converter_types_match=converter_types_match,
        has_converters=has_converters,
        outcome=outcome,
        labels=labels,
        min_turns=min_turns,
        max_turns=max_turns,
        limit=limit,
        cursor=cursor,
    )


@router.get(
    "/attack-options",
    response_model=AttackOptionsResponse,
)
async def get_attack_options() -> AttackOptionsResponse:
    """
    Get unique attack type names used across all attacks.

    Returns all attack type names found in stored attack results.
    Useful for populating attack type filter dropdowns in the GUI.

    Returns:
        AttackOptionsResponse: Sorted list of unique attack type names.
    """
    service = get_attack_service()
    type_names = await service.get_attack_options_async()
    return AttackOptionsResponse(attack_types=type_names)


@router.get(
    "/converter-options",
    response_model=ConverterOptionsResponse,
)
async def get_converter_options() -> ConverterOptionsResponse:
    """
    Get unique converter type names used across all attacks.

    Returns all converter type names found in stored attack results.
    Useful for populating converter filter dropdowns in the GUI.

    Returns:
        ConverterOptionsResponse: Sorted list of unique converter type names.
    """
    service = get_attack_service()
    type_names = await service.get_converter_options_async()
    return ConverterOptionsResponse(converter_types=type_names)


@router.post(
    "",
    response_model=CreateAttackResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ProblemDetail, "description": "Invalid request"},
        404: {"model": ProblemDetail, "description": "Target or converter not found"},
        422: {"model": ProblemDetail, "description": "Validation error"},
    },
)
async def create_attack(request: CreateAttackRequest) -> CreateAttackResponse:
    """
    Create a new attack.

    Establishes a new attack session with the specified target.
    Optionally specify source_conversation_id and cutoff_index to branch from an existing conversation.

    Returns:
        CreateAttackResponse: The created attack details.
    """
    service = get_attack_service()

    try:
        return await service.create_attack_async(request=request)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@router.get(
    "/{attack_result_id}",
    response_model=AttackSummary,
    responses={
        404: {"model": ProblemDetail, "description": "Attack not found"},
    },
)
async def get_attack(attack_result_id: str) -> AttackSummary:
    """
    Get attack details.

    Returns the attack metadata. Use GET /attacks/{id}/messages for messages.

    Returns:
        AttackSummary: Attack details.
    """
    service = get_attack_service()

    attack = await service.get_attack_async(attack_result_id=attack_result_id)
    if not attack:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attack '{attack_result_id}' not found",
        )

    return attack


@router.patch(
    "/{attack_result_id}",
    response_model=AttackSummary,
    responses={
        404: {"model": ProblemDetail, "description": "Attack not found"},
    },
)
async def update_attack(
    attack_result_id: str,
    request: UpdateAttackRequest,
) -> AttackSummary:
    """
    Update an attack's outcome.

    Used to mark attacks as success/failure/undetermined.

    Returns:
        AttackSummary: Updated attack details.
    """
    service = get_attack_service()

    attack = await service.update_attack_async(attack_result_id=attack_result_id, request=request)
    if not attack:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attack '{attack_result_id}' not found",
        )

    return attack


@router.get(
    "/{attack_result_id}/messages",
    response_model=ConversationMessagesResponse,
    responses={
        400: {"model": ProblemDetail, "description": "Invalid conversation"},
        404: {"model": ProblemDetail, "description": "Attack or conversation not found"},
    },
)
async def get_conversation_messages(
    attack_result_id: str,
    conversation_id: str = Query(..., description="The conversation_id whose messages to return"),
) -> ConversationMessagesResponse:
    """
    Get all messages for a conversation belonging to an attack.

    Returns prepended conversation and all messages in order.

    Returns:
        ConversationMessagesResponse: All messages for the conversation.
    """
    service = get_attack_service()

    try:
        messages = await service.get_conversation_messages_async(
            attack_result_id=attack_result_id,
            conversation_id=conversation_id,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    if not messages:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attack '{attack_result_id}' not found",
        )

    return messages


@router.get(
    "/{attack_result_id}/conversations",
    response_model=AttackConversationsResponse,
    responses={
        404: {"model": ProblemDetail, "description": "Attack not found"},
    },
)
async def get_conversations(attack_result_id: str) -> AttackConversationsResponse:
    """
    Get all conversations belonging to an attack.

    Returns the main conversation and all related conversations with
    message counts and preview text.

    Returns:
        AttackConversationsResponse: All conversations for the attack.
    """
    service = get_attack_service()

    result = await service.get_conversations_async(attack_result_id=attack_result_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attack '{attack_result_id}' not found",
        )

    return result


@router.post(
    "/{attack_result_id}/conversations",
    response_model=CreateConversationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"model": ProblemDetail, "description": "Attack not found"},
        400: {"model": ProblemDetail, "description": "Invalid request"},
    },
)
async def create_related_conversation(
    attack_result_id: str,
    request: CreateConversationRequest,
) -> CreateConversationResponse:
    """
    Create a new conversation within an existing attack.

    Generates a new conversation_id, adds it as a related conversation
    to the AttackResult, and optionally stores prepended messages.

    Returns:
        CreateConversationResponse: The new conversation details.
    """
    service = get_attack_service()

    try:
        result = await service.create_related_conversation_async(
            attack_result_id=attack_result_id,
            request=request,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attack '{attack_result_id}' not found",
        )

    return result


@router.post(
    "/{attack_result_id}/update-main-conversation",
    response_model=UpdateMainConversationResponse,
    responses={
        404: {"model": ProblemDetail, "description": "Attack not found"},
        400: {"model": ProblemDetail, "description": "Invalid conversation"},
    },
)
async def update_main_conversation(
    attack_result_id: str,
    request: UpdateMainConversationRequest,
) -> UpdateMainConversationResponse:
    """
    Change the main conversation for an attack.

    Swaps the attack's ``conversation_id`` to the specified conversation
    and moves the previous main into the related conversations list.

    Returns:
        UpdateMainConversationResponse: The AttackResult ID and new main conversation.
    """
    service = get_attack_service()

    try:
        result = await service.update_main_conversation_async(
            attack_result_id=attack_result_id,
            request=request,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attack '{attack_result_id}' not found",
        )

    return result


@router.post(
    "/{attack_result_id}/messages",
    response_model=AddMessageResponse,
    responses={
        404: {"model": ProblemDetail, "description": "Attack not found"},
        400: {"model": ProblemDetail, "description": "Message send failed"},
    },
)
async def add_message(
    attack_result_id: str,
    request: AddMessageRequest,
) -> AddMessageResponse:
    """
    Add a message to an attack.

    If send=True (default), sends the message to the target and waits for a response.
    If send=False, just stores the message in memory without sending (useful for
    system messages, context injection, or replaying assistant responses).

    Converters can be specified at three levels (in priority order):
    1. request.converter_ids - per-message converter instances
    2. request.converters - inline converter definitions
    3. attack.converter_ids - attack-level defaults

    Returns:
        AddMessageResponse: Updated attack with new message(s).
    """
    service = get_attack_service()

    try:
        return await service.add_message_async(attack_result_id=attack_result_id, request=request)
    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=error_msg,
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_msg,
        ) from e
    except Exception as e:
        logger.exception("Failed to add message to attack '%s'", attack_result_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error. Check server logs for details.",
        ) from e
