# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Converter-related request and response models.

This module defines the Instance models and preview functionality.
"""

from typing import Any

from pydantic import BaseModel, Field

from pyrit.backend.models.common import REGISTRY_INSTANCE_NAME_PATTERN
from pyrit.models import ConverterIdentifier, Parameter, PromptDataType

__all__ = [
    "ConverterCatalogEntry",
    "ConverterCatalogResponse",
    "ConverterInstance",
    "ConverterInstanceListResponse",
    "ConverterTypeEntry",
    "ConverterTypeResponse",
    "CreateConverterRequest",
    "CreateConverterResponse",
    "ConverterPreviewRequest",
    "ConverterPreviewResponse",
    "PreviewStep",
]


# ============================================================================
# Converter Types
# ============================================================================


class ConverterTypeEntry(BaseModel):
    """A converter type available from the backend registry."""

    converter_type: str = Field(..., description="Converter class name (e.g., 'Base64Converter')")
    supported_input_types: list[str] = Field(
        default_factory=list, description="Input data types supported by this converter type"
    )
    supported_output_types: list[str] = Field(
        default_factory=list, description="Output data types produced by this converter type"
    )
    parameters: list[Parameter] = Field(
        default_factory=list, description="Constructor parameters for dynamic form generation"
    )
    is_llm_based: bool = Field(False, description="Whether this converter requires an LLM target")
    description: str | None = Field(None, description="Short description of the converter from its docstring")


class ConverterTypeResponse(BaseModel):
    """Response for listing available converter types from the registry."""

    items: list[ConverterTypeEntry] = Field(..., description="List of available converter types")


# LEGACY COMPATIBILITY: ``Catalog`` is the pre-registry name for ``Type``. These
# aliases exist only so the un-migrated chat UI keeps working; delete them with the
# /catalog route when that UI switches to the /types API.
ConverterCatalogEntry = ConverterTypeEntry
ConverterCatalogResponse = ConverterTypeResponse


# ============================================================================
# Converter Instances (Runtime Objects)
# ============================================================================


class ConverterInstance(BaseModel):
    """
    A registered converter instance.

    Pairs the registry instance id with the converter's ``ConverterIdentifier`` —
    the typed identity/configuration projection that is the single source of truth
    for the converter's class, supported data types, and constructor params.
    """

    converter_id: str = Field(..., description="Converter instance registry name")
    identifier: ConverterIdentifier = Field(..., description="The converter's identity/configuration projection")
    is_llm_based: bool = Field(False, description="Whether this converter requires an LLM target")
    description: str | None = Field(None, description="Short description of the converter type")


class ConverterInstanceListResponse(BaseModel):
    """Response for listing converter instances."""

    items: list[ConverterInstance] = Field(..., description="List of converter instances")


class CreateConverterRequest(BaseModel):
    """Request to create a new converter instance."""

    # LEGACY COMPATIBILITY: The current chat UI does not send a name. Make this
    # field required when the chat-migration stack layer sends explicit names.
    name: str | None = Field(
        None,
        min_length=1,
        pattern=REGISTRY_INSTANCE_NAME_PATTERN,
        description="Unique registry name; omitted only for legacy chat compatibility",
    )
    type: str = Field(..., description="Converter type (e.g., 'Base64Converter')")
    # LEGACY COMPATIBILITY: The former create response echoed this field. Remove
    # it after clients use the complete ConverterInstance response.
    display_name: str | None = Field(None, description="Human-readable display name")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Converter constructor parameters",
    )


class CreateConverterResponse(BaseModel):
    """
    Legacy response model for downstream imports.

    POST /converters now returns ``ConverterInstance``. Remove this model when
    downstream clients no longer import the former response type.
    """

    converter_id: str = Field(..., description="Unique converter instance identifier")
    converter_type: str = Field(..., description="Converter class name")
    display_name: str | None = Field(None, description="Human-readable display name")


# ============================================================================
# Converter Preview
# ============================================================================


class PreviewStep(BaseModel):
    """A single step in the conversion preview."""

    converter_id: str = Field(..., description="Converter instance ID")
    converter_type: str = Field(..., description="Converter type")
    input_value: str = Field(..., description="Input to this converter")
    input_data_type: PromptDataType = Field(..., description="Input data type")
    output_value: str = Field(..., description="Output from this converter")
    output_data_type: PromptDataType = Field(..., description="Output data type")


class ConverterPreviewRequest(BaseModel):
    """Request to preview converter transformation."""

    original_value: str = Field(..., description="Text to convert")
    original_value_data_type: PromptDataType = Field(default="text", description="Data type of original value")
    converter_ids: list[str] = Field(..., description="Converter instance IDs to apply")


class ConverterPreviewResponse(BaseModel):
    """Response from converter preview."""

    original_value: str = Field(..., description="Original input text")
    original_value_data_type: PromptDataType = Field(..., description="Data type of original value")
    converted_value: str = Field(..., description="Final converted text")
    converted_value_data_type: PromptDataType = Field(..., description="Data type of converted value")
    steps: list[PreviewStep] = Field(..., description="Step-by-step conversion results")
