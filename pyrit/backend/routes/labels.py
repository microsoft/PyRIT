# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Labels API routes.

Provides access to unique label values for filtering in the GUI.
"""

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from pyrit.memory import CentralMemory

router = APIRouter(prefix="/labels", tags=["labels"])


class LabelOptionsResponse(BaseModel):
    """Response containing unique label keys and their values."""

    source: str = Field(..., description="Source type (e.g., 'attacks')")
    labels: dict[str, list[str]] = Field(..., description="Map of label keys to their unique values")


@router.get(
    "",
    response_model=LabelOptionsResponse,
)
async def get_label_options(
    source: Literal["attacks"] = Query(
        "attacks",
        description="Source type to get labels from. Currently only 'attacks' is supported.",
    ),
) -> LabelOptionsResponse:
    """
    Get unique label keys and values for filtering.

    Returns all unique label key-value combinations from the specified source.
    Useful for populating filter dropdowns in the GUI.

    Args:
        source: The source type to query labels from.

    Returns:
        LabelOptionsResponse: Map of label keys to their unique values.
    """
    memory = CentralMemory.get_memory_instance()

    labels = memory.get_unique_attack_labels() if source == "attacks" else {}

    return LabelOptionsResponse(source=source, labels=labels)
