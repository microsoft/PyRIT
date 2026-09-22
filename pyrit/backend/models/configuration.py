# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Configuration file API models."""

from pydantic import BaseModel, Field


class ConfigurationFileContent(BaseModel):
    """Raw UTF-8 contents of the backend configuration file."""

    content: str = Field(..., description="Raw YAML configuration file contents")
    source: str = Field(..., description="Configuration file path or credential-free blob URI")
    version: str = Field(..., description="Opaque version token for optimistic concurrency")


class UpdateConfigurationFileRequest(BaseModel):
    """Replacement contents for the backend configuration file."""

    content: str = Field(..., description="Raw YAML configuration file contents")
    version: str = Field(..., description="Version token returned by the latest content read")


class EnvironmentFileContent(BaseModel):
    """An environment file available to the backend."""

    id: str = Field(..., description="Stable identifier used to update this file")
    name: str = Field(..., description="Environment file name")
    path: str = Field(..., description="Resolved environment file path")
    content: str = Field(..., description="Raw dotenv file contents")
    exists: bool = Field(..., description="Whether the environment file currently exists")
    version: str | None = Field(None, description="Opaque version token for optimistic concurrency")
    read_only: bool = Field(False, description="Whether this source can be updated through the backend")
    read_only_reason: str | None = Field(None, description="Reason this source cannot be updated")


class EnvironmentFileListResponse(BaseModel):
    """Environment files configured for the backend."""

    items: list[EnvironmentFileContent] = Field(..., description="Environment files in load order")


class UpdateEnvironmentFileRequest(BaseModel):
    """Replacement contents for an environment file."""

    content: str = Field(..., description="Raw dotenv file contents")
    version: str = Field(..., description="Version token returned by the latest content read")
