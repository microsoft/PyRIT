# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Awaitable
from typing import Annotated, TypeVar
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from pyrit.backend.models.operations import (
    FindingEvidenceAttachResponse,
    FindingEvidenceCreateRequest,
    FindingEvidenceListResponse,
    FindingListResponse,
    FindingOptionsResponse,
    OperationListResponse,
)
from pyrit.backend.services.operation_service import (
    DuplicateOperationError,
    FindingEvidenceAttributionError,
    FindingEvidenceNotFoundError,
    FindingNotFoundError,
    OperationNotFoundError,
    OperationService,
)
from pyrit.models import Finding, FindingCreate, Operation, OperationCreate
from pyrit.models.harm_category import HarmCategory

T = TypeVar("T")

router = APIRouter(prefix="/operations", tags=["operations"])


@router.post("", response_model=Operation, status_code=status.HTTP_201_CREATED)
async def create_operation_async(request: OperationCreate) -> Operation:
    """
    Create an operation with a name unique after trimming and case folding.

    Returns:
        Operation: The saved operation.

    Raises:
        HTTPException: 409 with the existing operation when the name is taken.
    """
    try:
        return await OperationService().create_async(request)
    except DuplicateOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "operation": exc.existing.model_dump(mode="json")},
        ) from exc


@router.get("", response_model=OperationListResponse)
async def list_operations_async() -> OperationListResponse:
    """
    List all operations.

    Returns:
        OperationListResponse: Operations in name order.
    """
    return OperationListResponse(items=await OperationService().list_async())


@router.get("/finding-options", response_model=FindingOptionsResponse)
async def get_finding_options_async() -> FindingOptionsResponse:
    """
    Read the canonical harm categories for findings.

    Returns:
        FindingOptionsResponse: Selectable categories, including Other.
    """
    return FindingOptionsResponse(harm_types=list(HarmCategory))


@router.get("/{operation_id}", response_model=Operation)
async def get_operation_async(operation_id: UUID) -> Operation:
    """
    Read one operation.

    Returns:
        Operation: The requested operation.
    """
    return await _require_operation_async(OperationService().get_async(operation_id))


@router.post("/{operation_id}/findings", response_model=Finding, status_code=status.HTTP_201_CREATED)
async def create_operation_finding_async(*, operation_id: UUID, request: FindingCreate) -> Finding:
    """
    Record a finding within an operation.

    Returns:
        Finding: The saved finding.
    """
    return await _require_operation_async(
        OperationService().create_finding_async(operation_id=operation_id, request=request)
    )


@router.get("/{operation_id}/findings", response_model=FindingListResponse)
async def list_operation_findings_async(
    *,
    operation_id: UUID,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    title: str | None = None,
) -> FindingListResponse:
    """
    List one page of an operation's findings, by severity and then newest first.

    Returns:
        FindingListResponse: A bounded page.
    """
    return await _require_operation_async(
        OperationService().list_findings_async(operation_id=operation_id, limit=limit, offset=offset, title_query=title)
    )


@router.put("/{operation_id}/findings/{finding_id}", response_model=Finding)
async def update_operation_finding_async(*, operation_id: UUID, finding_id: UUID, request: FindingCreate) -> Finding:
    """
    Replace the editable fields of a finding in its operation.

    Returns:
        Finding: The updated finding.
    """
    return await _require_operation_async(
        OperationService().update_finding_async(operation_id=operation_id, finding_id=finding_id, request=request)
    )


@router.delete("/{operation_id}/findings/{finding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_operation_finding_async(*, operation_id: UUID, finding_id: UUID) -> Response:
    """
    Permanently remove a finding from its operation.

    Returns:
        Response: An empty success response.
    """
    await _require_operation_async(
        OperationService().delete_finding_async(operation_id=operation_id, finding_id=finding_id)
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{operation_id}/findings/{finding_id}/evidence", response_model=FindingEvidenceAttachResponse)
async def attach_finding_evidence_async(
    *, operation_id: UUID, finding_id: UUID, request: FindingEvidenceCreateRequest, response: Response
) -> FindingEvidenceAttachResponse:
    """
    Attach a saved conversation or return its unchanged existing association.

    Returns:
        FindingEvidenceAttachResponse: The association and creation indicator.
    """
    result = await _require_operation_async(
        OperationService().attach_finding_evidence_async(
            operation_id=operation_id, finding_id=finding_id, request=request
        )
    )
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return result


@router.get("/{operation_id}/findings/{finding_id}/evidence", response_model=FindingEvidenceListResponse)
async def list_finding_evidence_async(
    *,
    operation_id: UUID,
    finding_id: UUID,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FindingEvidenceListResponse:
    """
    Read a bounded evidence page with current source availability.

    Returns:
        FindingEvidenceListResponse: The page and continuation offset.
    """
    return await _require_operation_async(
        OperationService().list_finding_evidence_async(
            operation_id=operation_id, finding_id=finding_id, limit=limit, offset=offset
        )
    )


@router.delete("/{operation_id}/findings/{finding_id}/evidence/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def detach_finding_evidence_async(*, operation_id: UUID, finding_id: UUID, evidence_id: UUID) -> Response:
    """
    Remove an association without deleting the conversation.

    Returns:
        Response: An empty success response.
    """
    await _require_operation_async(
        OperationService().detach_finding_evidence_async(
            operation_id=operation_id, finding_id=finding_id, evidence_id=evidence_id
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _require_operation_async(pending: Awaitable[T]) -> T:
    try:
        return await pending
    except (OperationNotFoundError, FindingNotFoundError, FindingEvidenceNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except FindingEvidenceAttributionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
