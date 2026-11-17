# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pydantic import BaseModel, Field


class DatasetProviderInfo(BaseModel):
    """Facts and coarse estimates for a dataset provider's default configuration."""

    description: str | None = None
    source_url: str | None = None
    license_url: str | None = None
    access_url: str | None = None
    required_environment_variables: tuple[str, ...] = ()
    modalities: list[str] = Field(default_factory=list)
    harm_categories: list[str] = Field(default_factory=list)
    size: list[str] = Field(default_factory=list)
    load_time: list[str] = Field(default_factory=list)
    unavailable_reason: str | None = None
