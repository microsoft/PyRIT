# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Labels API routes.

Provides access to unique label values for filtering in the GUI.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from pyrit.backend.routes.common import parse_label_query_params
from pyrit.memory import CentralMemory

router = APIRouter(prefix="/labels", tags=["labels"])


class LabelOptionsResponse(BaseModel):
    """Response containing unique label keys and their values."""

    source: str = Field(..., description="Source type (e.g., 'attacks')")
    labels: dict[str, list[str]] = Field(..., description="Map of label keys to their unique values")
    operators: list[str] | None = Field(None, description="Unique attack operators")
    operations: list[str] | None = Field(None, description="Unique attack operations")


@router.get(
    "",
    response_model=LabelOptionsResponse,
    response_model_exclude_none=True,
)
async def get_label_options(  # pyrit-async-suffix-exempt
    source: Literal["attacks", "scenarios"] = Query(
        "attacks",
        description="Source type to get labels from.",
    ),
    operator: list[Annotated[str, Field(max_length=128)]] | None = Query(
        None,
        description="Narrow attack labels by operator.",
    ),
    operation: list[Annotated[str, Field(max_length=128)]] | None = Query(
        None,
        description="Narrow attack labels by operation.",
    ),
    label: list[str] | None = Query(None, description="Narrow attack labels by key:value filters."),
) -> LabelOptionsResponse:
    """
    Get unique label keys and values for filtering.

    Returns all unique label key-value combinations from the specified source.
    Useful for populating filter dropdowns in the GUI.

    Args:
        source: The source type to query labels from.
        operator: Operator values used to narrow attack rows.
        operation: Operation values used to narrow attack rows.
        label: Arbitrary key:value filters used to narrow attack rows.

    Returns:
        LabelOptionsResponse: Map of label keys to their unique values.
    """
    memory = CentralMemory.get_memory_instance()

    if source == "attacks":
        label_filters = parse_label_query_params(label)
        labels = await run_in_threadpool(
            memory.get_unique_attack_labels,
            operator=operator,
            operation=operation,
            labels=label_filters,
        )
        attribution = (
            {}
            if operator or operation or label_filters
            else await run_in_threadpool(memory.get_unique_attack_attribution)
        )
        return LabelOptionsResponse(source=source, labels=labels, **attribution)

    labels = await run_in_threadpool(memory.get_unique_scenario_labels)
    return LabelOptionsResponse(source=source, labels=labels)
