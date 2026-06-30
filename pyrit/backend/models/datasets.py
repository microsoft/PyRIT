# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset models for the PyRIT API.

Datasets are seed prompt/objective collections provided by
``SeedDatasetProvider`` subclasses. These models describe the wire format for
listing available datasets.
"""

from pydantic import BaseModel, Field


class DatasetInfo(BaseModel):
    """Metadata about a single available dataset."""

    name: str = Field(..., description="Dataset name (e.g., 'harmbench')")


class DatasetListResponse(BaseModel):
    """Response for listing available datasets."""

    items: list[DatasetInfo] = Field(..., description="List of available datasets")
