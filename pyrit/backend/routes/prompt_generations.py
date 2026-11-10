# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Prompt generation API routes.

Run generator requests as background jobs, review intermediate output, and save the result as a
named seed dataset. ``GET /prompt-generators`` describes each generator.
"""

from collections.abc import Awaitable
from typing import Annotated, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel

from pyrit.backend.middleware.auth import current_user_name
from pyrit.backend.models import ProblemDetail
from pyrit.backend.models.common import MAX_CURSOR_LENGTH
from pyrit.backend.models.prompt_generations import (
    PromptGenerationBatchCreateRequest,
    PromptGenerationCreateRequest,
    PromptGenerationDetail,
    PromptGenerationListResponse,
    PromptGenerationPreflightResponse,
    PromptGenerationRecord,
    PromptGenerationRequestFromDataset,
    PromptGenerationReview,
    PromptGenerationReviewUpdate,
    PromptGeneratorListResponse,
)
from pyrit.backend.services.prompt_generation_service import (
    PromptGenerationConflictError,
    PromptGenerationNotFoundError,
    get_prompt_generation_service,
    to_summary,
)
from pyrit.datasets import list_assert_behavior_presets, list_assert_suite_presets
from pyrit.models import AssertBehaviorPreset, AssertSuitePreset, PromptGenerationEstimate

T = TypeVar("T")

router = APIRouter(tags=["prompt-generations"])

_ERRORS: dict[int | str, dict[str, object]] = {
    404: {"model": ProblemDetail, "description": "Generation not found"},
    409: {
        "model": ProblemDetail,
        "description": "The generation's state doesn't allow this, or the dataset name is taken",
    },
}


class AssertBehaviorPresetListResponse(BaseModel):
    """Bundled ASSERT behavior presets."""

    items: list[AssertBehaviorPreset]


class AssertSuitePresetListResponse(BaseModel):
    """Bundled ASSERT suite presets."""

    items: list[AssertSuitePreset]


@router.get("/prompt-generators", response_model=PromptGeneratorListResponse)
async def list_prompt_generators_async() -> PromptGeneratorListResponse:
    """
    Describe the generators the backend can run.

    Returns:
        PromptGeneratorListResponse: Each generator's stages and target roles.
    """
    return PromptGeneratorListResponse(items=get_prompt_generation_service().list_generators())


@router.get("/prompt-generators/assert/behaviors", response_model=AssertBehaviorPresetListResponse)
async def list_assert_behaviors_async() -> AssertBehaviorPresetListResponse:
    """
    List the bundled ASSERT behavior presets, to start a policy from.

    Returns:
        AssertBehaviorPresetListResponse: Presets in name order.
    """
    return AssertBehaviorPresetListResponse(items=list(list_assert_behavior_presets().values()))


@router.get("/prompt-generators/assert/suites", response_model=AssertSuitePresetListResponse)
async def list_assert_suites_async() -> AssertSuitePresetListResponse:
    """
    List the bundled ASSERT suites: an application context and the behaviors to evaluate in it.

    Returns:
        AssertSuitePresetListResponse: Suites in name order.
    """
    return AssertSuitePresetListResponse(items=list(list_assert_suite_presets().values()))


@router.get("/prompt-generations", response_model=PromptGenerationListResponse)
async def list_prompt_generations_async() -> PromptGenerationListResponse:
    """
    List generations, newest first. Generations created in one batch share a ``group_id``.

    Returns:
        PromptGenerationListResponse: Generations without their requests.
    """
    records = await get_prompt_generation_service().list_async()
    return PromptGenerationListResponse(items=[to_summary(record) for record in records])


@router.post(
    "/prompt-generations",
    response_model=PromptGenerationDetail,
    status_code=status.HTTP_201_CREATED,
    responses={409: _ERRORS[409]},
)
async def create_prompt_generation_async(
    request: PromptGenerationCreateRequest,
    user: Annotated[str | None, Depends(current_user_name)],
) -> PromptGenerationDetail:
    """
    Queue a generation.

    Returns:
        PromptGenerationDetail: The queued generation.
    """
    record = await _handle_async(get_prompt_generation_service().create_async(request=request, created_by=user))
    return _detail(record)


@router.post(
    "/prompt-generations/batch",
    response_model=PromptGenerationListResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: _ERRORS[409]},
)
async def create_prompt_generation_batch_async(
    request: PromptGenerationBatchCreateRequest,
    user: Annotated[str | None, Depends(current_user_name)],
) -> PromptGenerationListResponse:
    """
    Queue generations that share a generator, targets, and settings, such as an ASSERT suite.

    Returns:
        PromptGenerationListResponse: The queued generations, sharing a ``group_id``.
    """
    records = await _handle_async(get_prompt_generation_service().create_batch_async(request=request, created_by=user))
    return PromptGenerationListResponse(items=[to_summary(record) for record in records])


@router.post("/prompt-generations/preflight", response_model=PromptGenerationPreflightResponse)
async def preflight_prompt_generation_async(
    request: PromptGenerationCreateRequest,
) -> PromptGenerationPreflightResponse:
    """
    Check a request and estimate its work without saving it or calling a target.

    Returns:
        PromptGenerationPreflightResponse: Validation errors, name availability, and an estimate.
    """
    return await get_prompt_generation_service().preflight_async(request)


@router.get(
    "/prompt-generations/from-dataset",
    response_model=PromptGenerationRequestFromDataset,
    responses={404: {"model": ProblemDetail, "description": "The dataset doesn't record a generation request"}},
)
async def get_prompt_generation_request_from_dataset_async(
    selection_key: Annotated[
        str, Query(max_length=MAX_CURSOR_LENGTH, description="The selection_key of a dataset from GET /datasets")
    ],
) -> PromptGenerationRequestFromDataset:
    """
    Reconstruct the request that generated a stored dataset, to edit and regenerate it.

    Returns:
        PromptGenerationRequestFromDataset: The generator and its request.
    """
    return await _handle_async(get_prompt_generation_service().request_from_dataset_async(selection_key))


@router.get("/prompt-generations/{generation_id}", response_model=PromptGenerationDetail, responses=_ERRORS)
async def get_prompt_generation_async(generation_id: UUID) -> PromptGenerationDetail:
    """
    Read one generation.

    Returns:
        PromptGenerationDetail: The generation with its request and result summary.
    """
    return _detail(await _handle_async(get_prompt_generation_service().get_async(str(generation_id))))


@router.delete("/prompt-generations/{generation_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_ERRORS)
async def delete_prompt_generation_async(generation_id: UUID) -> Response:
    """
    Delete a generation that isn't active. A saved dataset is kept.

    Returns:
        Response: An empty success response.
    """
    await _handle_async(get_prompt_generation_service().delete_async(str(generation_id)))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/prompt-generations/{generation_id}/continue", response_model=PromptGenerationDetail, responses=_ERRORS)
async def continue_prompt_generation_async(generation_id: UUID) -> PromptGenerationDetail:
    """
    Finish the review and queue the remaining stages.

    Returns:
        PromptGenerationDetail: The queued generation.
    """
    return _detail(await _handle_async(get_prompt_generation_service().continue_async(str(generation_id))))


@router.post("/prompt-generations/{generation_id}/retry", response_model=PromptGenerationDetail, responses=_ERRORS)
async def retry_prompt_generation_async(generation_id: UUID) -> PromptGenerationDetail:
    """
    Resume a failed, cancelled, or interrupted generation from its last checkpoint.

    Returns:
        PromptGenerationDetail: The queued generation.
    """
    return _detail(await _handle_async(get_prompt_generation_service().retry_async(str(generation_id))))


@router.post("/prompt-generations/{generation_id}/cancel", response_model=PromptGenerationDetail, responses=_ERRORS)
async def cancel_prompt_generation_async(generation_id: UUID) -> PromptGenerationDetail:
    """
    Cancel a generation. Its checkpoint is kept, so it can be retried.

    Returns:
        PromptGenerationDetail: The cancelled generation.
    """
    return _detail(await _handle_async(get_prompt_generation_service().cancel_async(str(generation_id))))


@router.get("/prompt-generations/{generation_id}/review", response_model=PromptGenerationReview, responses=_ERRORS)
async def get_prompt_generation_review_async(generation_id: UUID) -> PromptGenerationReview:
    """
    Read output awaiting review.

    Returns:
        PromptGenerationReview: The editable output.
    """
    return await _handle_async(get_prompt_generation_service().get_review_async(str(generation_id)))


@router.put("/prompt-generations/{generation_id}/review", response_model=PromptGenerationReview, responses=_ERRORS)
async def save_prompt_generation_review_async(
    *, generation_id: UUID, update: PromptGenerationReviewUpdate
) -> PromptGenerationReview:
    """
    Save edited review output.

    Returns:
        PromptGenerationReview: The saved output.
    """
    return await _handle_async(
        get_prompt_generation_service().save_review_async(generation_id=str(generation_id), update=update)
    )


@router.get("/prompt-generations/{generation_id}/estimate", response_model=PromptGenerationEstimate, responses=_ERRORS)
async def get_prompt_generation_estimate_async(generation_id: UUID) -> PromptGenerationEstimate:
    """
    Estimate a generation's remaining work from its progress, without calling a target.

    Returns:
        PromptGenerationEstimate: Remaining calls and an approximate token range.
    """
    return await _handle_async(get_prompt_generation_service().get_estimate_async(str(generation_id)))


async def _handle_async(pending: Awaitable[T]) -> T:
    try:
        return await pending
    except PromptGenerationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except PromptGenerationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


def _detail(record: PromptGenerationRecord) -> PromptGenerationDetail:
    return PromptGenerationDetail.model_validate(record.model_dump(include=set(PromptGenerationDetail.model_fields)))
