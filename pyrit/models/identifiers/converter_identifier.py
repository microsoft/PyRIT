# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Strongly-typed projection of a converter's identifier."""

from __future__ import annotations

from typing import Annotated, ClassVar

from pyrit.models.identifiers.component_identifier import ComponentIdentifier
from pyrit.models.identifiers.evaluation_markers import Evaluate
from pyrit.models.identifiers.param_markers import Param
from pyrit.models.identifiers.target_identifier import (  # noqa: TC001
    TargetIdentifier,  # runtime-required by Pydantic field annotations
)
from pyrit.models.literals import PromptDataType  # noqa: TC001  (runtime-required by Pydantic field annotations)
from pyrit.models.parameter import ComponentType


class ConverterIdentifier(ComponentIdentifier):
    """
    Strongly-typed projection of a ``PromptConverter``'s ``ComponentIdentifier``.

    Promotes the supported input/output data types; any converter-specific params
    stay in ``params``. The converter's own child slots — ``converter_target``
    (an LLM target) and ``sub_converter`` (a wrapped converter) — are promoted to
    typed fields.

    Build markers (``Param.*``) declare how these fields map to the converter's
    constructor: the supported-type lists are class attributes sourced from the
    converter class (``Param.ClassAttr``), while ``converter_target`` and
    ``sub_converter`` are included constructor parameters whose identifier types
    make them references resolved from the target and converter registries.
    """

    component_type: ClassVar[ComponentType] = ComponentType.CONVERTER

    #: Input data types supported by this converter (sourced from the
    #: ``SUPPORTED_INPUT_TYPES`` class attribute, not a ctor arg).
    supported_input_types: Annotated[list[PromptDataType] | None, Evaluate.Include(), Param.ClassAttr()] = None
    #: Output data types produced by this converter (sourced from the
    #: ``SUPPORTED_OUTPUT_TYPES`` class attribute, not a ctor arg).
    supported_output_types: Annotated[list[PromptDataType] | None, Evaluate.Include(), Param.ClassAttr()] = None
    #: Target an LLM-backed converter calls (e.g., ``LLMGenericTextConverter``).
    converter_target: Annotated[TargetIdentifier | None, Evaluate.Include(), Param.Include()] = None
    #: A nested converter a composite wraps (e.g., ``SelectiveTextConverter``),
    #: typed recursively. An included constructor parameter resolved by name from
    #: the converter registry.
    sub_converter: Annotated[ConverterIdentifier | None, Evaluate.Include(), Param.Include()] = None
