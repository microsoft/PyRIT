# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Converter mappers – domain → DTO translation for converter-related models.

``ConverterInstance`` pairs the registry instance id with the converter's
``ConverterIdentifier`` — the typed, lossless identity projection of the
converter's ``ComponentIdentifier`` — which is the single source of truth on the
wire for the converter's class, supported data types, and constructor params.
"""

from pyrit.backend.models import ConverterInstance
from pyrit.converter import Converter
from pyrit.models import ConverterIdentifier


def converter_object_to_instance(
    *,
    converter_id: str,
    converter_obj: Converter,
    is_llm_based: bool,
    description: str | None,
) -> ConverterInstance:
    """
    Build a ConverterInstance DTO from a registry converter object.

    Pairs the registry instance id with the converter's ``ConverterIdentifier``,
    the typed identity/configuration projection used as the single source of truth
    on the wire.

    Args:
        converter_id (str): The unique converter instance identifier.
        converter_obj (Converter): The domain Converter object from the registry.
        is_llm_based (bool): Whether the converter class requires an LLM target.
        description (str | None): The converter class description.

    Returns:
        ConverterInstance DTO wrapping the converter's identifier.
    """
    return ConverterInstance(
        converter_id=converter_id,
        identifier=ConverterIdentifier.from_component_identifier(converter_obj.get_identifier()),
        is_llm_based=is_llm_based,
        description=description,
    )
