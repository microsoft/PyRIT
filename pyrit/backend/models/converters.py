# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Converter-related request and response models.

This module defines the Instance models and preview functionality.
"""

from typing import Any

from pydantic import BaseModel, Field, model_validator

from pyrit.backend.models.common import MAX_ITEMS, REGISTRY_INSTANCE_NAME_PATTERN, IdentifierStr
from pyrit.models import MEDIA_PATH_DATA_TYPES, ConverterIdentifier, Parameter, PromptDataType

__all__ = [
    "ConverterInstance",
    "ConverterInstanceListResponse",
    "ConverterTypeEntry",
    "ConverterTypeResponse",
    "CreateConverterRequest",
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

    name: str = Field(
        ...,
        min_length=1,
        pattern=REGISTRY_INSTANCE_NAME_PATTERN,
        description="Unique registry name for the converter instance",
    )
    type: IdentifierStr = Field(..., description="Converter type (e.g., 'Base64Converter')")
    params: dict[IdentifierStr, Any] = Field(
        default_factory=dict,
        max_length=MAX_ITEMS,
        description="Converter constructor parameters",
    )


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
    import_url: bool = Field(
        False,
        description="Download an http(s) original_value once into managed storage instead of passing the URL on. "
        "The stored copy keeps the declared image_path, audio_path, video_path, or binary_path type.",
    )

    @model_validator(mode="after")
    def _validate_import_url(self) -> "ConverterPreviewRequest":
        """
        Validate that a URL import names the media type to store.

        Returns:
            The validated preview request.

        Raises:
            ValueError: If import_url is set without a media path type.
        """
        if self.import_url and self.original_value_data_type not in MEDIA_PATH_DATA_TYPES:
            raise ValueError(
                "import_url needs an image_path, audio_path, video_path, or binary_path original_value_data_type; "
                "declare the media type to store the URL as."
            )
        return self


class ConverterPreviewResponse(BaseModel):
    """Response from converter preview."""

    original_value: str = Field(
        ..., description="Original input, or the stored copy that conversion used when an http(s) URL was imported"
    )
    original_value_data_type: PromptDataType = Field(..., description="Data type of original value")
    converted_value: str = Field(..., description="Final converted text")
    converted_value_data_type: PromptDataType = Field(..., description="Data type of converted value")
    steps: list[PreviewStep] = Field(..., description="Step-by-step conversion results")
    prompt_metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Source information for an imported original value. Send it as the piece's prompt_metadata "
        "with the stored copy to keep it in the conversation.",
    )
