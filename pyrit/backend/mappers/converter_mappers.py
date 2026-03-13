# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Converter mappers – domain → DTO translation for converter-related models.
"""

from typing import Optional

from pyrit.backend.models.converters import ConverterInstance
from pyrit.prompt_converter import PromptConverter

# Base keys from PromptConverter._create_identifier that are NOT converter-specific
_BASE_CONVERTER_PARAM_KEYS = {
    "supported_input_types",
    "supported_output_types",
}


def converter_object_to_instance(
    converter_id: str,
    converter_obj: PromptConverter,
    *,
    sub_converter_ids: Optional[list[str]] = None,
) -> ConverterInstance:
    """
    Build a ConverterInstance DTO from a registry converter object.

    Extracts only the frontend-relevant fields from the internal identifier,
    avoiding leakage of internal PyRIT core structures.

    Args:
        converter_id: The unique converter instance identifier.
        converter_obj: The domain PromptConverter object from the registry.
        sub_converter_ids: Optional list of registered converter IDs for sub-converters.

    Returns:
        ConverterInstance DTO with metadata derived from the object.
    """
    identifier = converter_obj.get_identifier()

    supported_input = identifier.params.get("supported_input_types")
    supported_output = identifier.params.get("supported_output_types")

    # Extract converter-specific params by filtering out base keys
    converter_specific = {k: v for k, v in identifier.params.items() if k not in _BASE_CONVERTER_PARAM_KEYS} or None

    return ConverterInstance(
        converter_id=converter_id,
        converter_type=identifier.class_name,
        display_name=None,
        supported_input_types=list(supported_input) if supported_input else [],
        supported_output_types=list(supported_output) if supported_output else [],
        converter_specific_params=converter_specific,
        sub_converter_ids=sub_converter_ids,
    )
