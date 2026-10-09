# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Converter-related request and response models.

This module defines the Instance models and preview functionality.
"""

from typing import Any

from pydantic import BaseModel, Field

from pyrit.backend.models.common import MAX_ITEMS, REGISTRY_INSTANCE_NAME_PATTERN, IdentifierStr
from pyrit.models import ConverterIdentifier, Parameter, PromptDataType
from pyrit.models.catalog.instance_recipe import CredentialReference, UnrestorableInstance

__all__ = [
    "ConverterInstance",
    "ConverterInstanceListResponse",
    "ConverterSettings",
    "ConverterTypeEntry",
    "ConverterTypeResponse",
    "CreateConverterRequest",
    "ConverterPreviewRequest",
    "ConverterPreviewResponse",
    "PreviewStep",
    "UpdateConverterRequest",
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
    version: str | None = Field(
        None, description="Version of the saved converter, required to change or delete it; None if not saved"
    )


class ConverterInstanceListResponse(BaseModel):
    """Response for listing converter instances."""

    items: list[ConverterInstance] = Field(..., description="List of converter instances")
    unrestorable: list[UnrestorableInstance] = Field(
        default_factory=list, description="Saved converters the last restore could not rebuild, with the reasons"
    )
    restore_error: str | None = Field(
        None, description="Why the last restore could not read the saved instance store, if it could not"
    )


class ConverterSettings(BaseModel):
    """The type, parameters, and credentials a converter is built from."""

    type: IdentifierStr = Field(..., description="Converter type (e.g., 'Base64Converter')")
    params: dict[IdentifierStr, Any] = Field(
        default_factory=dict,
        max_length=MAX_ITEMS,
        description="Converter constructor parameters",
    )
    credentials: dict[IdentifierStr, CredentialReference] = Field(
        default_factory=dict,
        max_length=MAX_ITEMS,
        description=(
            "Credential parameters read from server environment variables. Only the variable name is saved. "
            "Requires administrator access."
        ),
    )


class CreateConverterRequest(ConverterSettings):
    """Request to create a new converter instance."""

    name: str = Field(
        ...,
        min_length=1,
        pattern=REGISTRY_INSTANCE_NAME_PATTERN,
        description="Unique registry name for the converter instance",
    )


class UpdateConverterRequest(ConverterSettings):
    """Request to replace a saved converter; parameters and credentials it omits are removed."""

    version: IdentifierStr = Field(..., description="Version returned when the converter was read")


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
    converter_ids: list[IdentifierStr] = Field(..., max_length=MAX_ITEMS, description="Converter instance IDs to apply")
    start_token: str = Field(default="⟪", min_length=1, description="Opening marker for selected text regions")
    end_token: str = Field(default="⟫", min_length=1, description="Closing marker for selected text regions")


class ConverterPreviewResponse(BaseModel):
    """Response from converter preview."""

    original_value: str = Field(..., description="Original input text")
    original_value_data_type: PromptDataType = Field(..., description="Data type of original value")
    converted_value: str = Field(..., description="Final converted text")
    converted_value_data_type: PromptDataType = Field(..., description="Data type of converted value")
    steps: list[PreviewStep] = Field(..., description="Step-by-step conversion results")
