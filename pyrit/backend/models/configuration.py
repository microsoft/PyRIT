# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Configuration file API models."""

from pydantic import BaseModel, Field


class ConfigurationFileContent(BaseModel):
    """Raw UTF-8 contents of the backend configuration file."""

    content: str = Field(..., description="Raw YAML configuration file contents")
    source: str = Field(..., description="Configuration file path or credential-free blob URI")


class UpdateConfigurationFileRequest(BaseModel):
    """Replacement contents for the backend configuration file."""

    content: str = Field(..., description="Raw YAML configuration file contents")


class EnvironmentFileContent(BaseModel):
    """An environment file available to the backend."""

    id: str = Field(..., description="Stable identifier used to update this file")
    name: str = Field(..., description="Environment file name")
    path: str = Field(..., description="Resolved environment file path")
    content: str = Field(..., description="Raw dotenv file contents")
    exists: bool = Field(..., description="Whether the environment file currently exists")


class EnvironmentFileListResponse(BaseModel):
    """Environment files configured for the backend."""

    items: list[EnvironmentFileContent] = Field(..., description="Environment files in load order")


class UpdateEnvironmentFileRequest(BaseModel):
    """Replacement contents for an environment file."""

    content: str = Field(..., description="Raw dotenv file contents")
