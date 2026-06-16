# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Converter mappers – domain → DTO translation for converter-related models.

Identity vs. presentation:
``ConverterIdentifier`` is the typed, lossless
*identity* projection of a converter's ``ComponentIdentifier``;
``ConverterInstance`` is the backend *presentation* view (adds ``converter_id``
binding, ``display_name``, and ``sub_converter_ids``).
"""

from pyrit.backend.models import ConverterInstance
from pyrit.models import ConverterIdentifier
from pyrit.prompt_converter import PromptConverter


def converter_object_to_instance(
    converter_id: str,
    converter_obj: PromptConverter,
    *,
    sub_converter_ids: list[str] | None = None,
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
    converter_identifier = ConverterIdentifier.from_component_identifier(converter_obj.get_identifier())

    supported_input = converter_identifier.supported_input_types
    supported_output = converter_identifier.supported_output_types

    # supported_input/output_types are promoted to typed fields and mirrored into
    # params; strip them so only converter-specific params remain.
    promoted_param_names = set(ConverterIdentifier._promoted_param_fields())
    converter_specific = {k: v for k, v in converter_identifier.params.items() if k not in promoted_param_names} or None

    return ConverterInstance(
        converter_id=converter_id,
        converter_type=converter_identifier.class_name,
        display_name=None,
        supported_input_types=list(supported_input) if supported_input else [],
        supported_output_types=list(supported_output) if supported_output else [],
        converter_specific_params=converter_specific,
        sub_converter_ids=sub_converter_ids,
    )
