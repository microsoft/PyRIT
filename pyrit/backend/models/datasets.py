# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset models for the PyRIT API.

Datasets are seed prompt/objective collections provided by
``SeedDatasetProvider`` subclasses. These models describe the wire format for
listing available datasets and loading them into memory.
"""

from pydantic import BaseModel, Field


class DatasetInfo(BaseModel):
    """Metadata about a single available dataset."""

    name: str = Field(..., description="Dataset name (e.g., 'harmbench')")
    loaded: bool = Field(False, description="Whether the dataset is already present in memory")


class DatasetListResponse(BaseModel):
    """Response for listing available datasets."""

    items: list[DatasetInfo] = Field(..., description="List of available datasets")


class LoadDatasetRequest(BaseModel):
    """Request to load one or more datasets into memory."""

    dataset_names: list[str] = Field(..., description="Names of the datasets to load into memory")
    cache: bool = Field(True, description="Whether to cache fetched remote datasets to disk")


class LoadedDataset(BaseModel):
    """Summary of a dataset that was loaded into memory."""

    name: str = Field(..., description="Dataset name")
    seed_count: int = Field(..., description="Number of seeds loaded for this dataset")


class LoadDatasetResponse(BaseModel):
    """Response for loading datasets into memory."""

    loaded_datasets: list[LoadedDataset] = Field(..., description="Datasets that were loaded into memory")
    total_seeds: int = Field(..., description="Total number of seeds loaded across all datasets")
