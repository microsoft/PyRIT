# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Strongly-typed projection of a converter's identifier."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from pyrit.models.identifiers.component_identifier import ComponentIdentifier
from pyrit.models.identifiers.evaluation_markers import Evaluate
from pyrit.models.identifiers.target_identifier import (  # noqa: TC001
    TargetIdentifier,  # runtime-required by Pydantic field annotations
)
from pyrit.models.literals import PromptDataType  # noqa: TC001  (runtime-required by Pydantic field annotations)


class ConverterIdentifier(ComponentIdentifier):
    """
    Strongly-typed projection of a ``PromptConverter``'s ``ComponentIdentifier``.

    Promotes the supported input/output data types; any converter-specific params
    stay in ``params``. The converter's own child slots — ``converter_target``
    (an LLM target) and ``sub_converters`` (nested converters) — are promoted to
    typed fields.
    """

    #: Input data types supported by this converter.
    supported_input_types: Annotated[list[PromptDataType] | None, Evaluate.Include()] = None
    #: Output data types produced by this converter.
    supported_output_types: Annotated[list[PromptDataType] | None, Evaluate.Include()] = None
    #: Target an LLM-backed converter calls (e.g., ``LLMGenericTextConverter``).
    converter_target: Annotated[TargetIdentifier | None, Evaluate.Include()] = None
    #: Nested converters a composite wraps (e.g., ``SelectiveTextConverter``),
    #: typed recursively.
    sub_converters: Annotated[list[ConverterIdentifier], Evaluate.Include()] = Field(default_factory=list)
