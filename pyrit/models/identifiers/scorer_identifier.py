# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Strongly-typed projection of a scorer's identifier."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, ClassVar

from pydantic import Field

from pyrit.models.identifiers.component_identifier import ComponentIdentifier
from pyrit.models.identifiers.evaluation_markers import Evaluate
from pyrit.models.identifiers.param_markers import Param
from pyrit.models.identifiers.target_identifier import (  # noqa: TC001
    TargetIdentifier,  # runtime-required by Pydantic field annotations
)
from pyrit.models.parameter import ComponentType

if TYPE_CHECKING:
    from collections.abc import Mapping


class ScorerIdentifier(ComponentIdentifier):
    """
    Strongly-typed projection of a ``Scorer``'s ``ComponentIdentifier``.

    Promotes the ``scorer_type`` discriminator, the ``score_aggregator`` name, and
    the scorer's own child slots — ``prompt_target`` (an LLM target) and
    ``sub_scorers`` (nested scorers).

    Build markers (``Param.*``) declare how the child slots map to the scorer's
    constructor: ``prompt_target`` is an included parameter aliased to the
    ``chat_target`` constructor arg, and ``sub_scorers`` is an included parameter
    aliased to the composite scorer's ``scorers`` arg. Their identifier types make
    them references resolved by name from the target and scorer registries.

    The optional ``sub_scorers_order_independent`` param declares that the
    aggregation verdict does not depend on child order.

    A source-backed scorer's ``source`` constructor argument is a reference
    resolved by name from the source registry. The source's identity already sits
    in ``children["source"]``, so it is declared in ``constructor_references``
    rather than promoted to a typed field.
    """

    component_type: ClassVar[ComponentType | None] = ComponentType.SCORER
    constructor_references: ClassVar[Mapping[str, ComponentType]] = MappingProxyType({"source": ComponentType.SOURCE})

    #: The scorer category (e.g., ``"true_false"`` or ``"float_scale"``).
    scorer_type: Annotated[str | None, Evaluate.Include()] = None
    #: Name of the aggregator function combining sub-scores (e.g., ``"AND_"``).
    score_aggregator: Annotated[str | None, Evaluate.Include()] = None
    #: Target an LLM-backed scorer calls (e.g., ``SelfAskScaleScorer``). The
    #: constructor arg is ``chat_target``, so the build marker aliases it.
    prompt_target: Annotated[TargetIdentifier | None, Evaluate.Include(), Param.Include(alias="chat_target")] = None
    #: Nested scorers a composite wraps, typed recursively. The composite
    #: constructor arg is ``scorers`` (a list), so the build marker aliases it.
    sub_scorers: Annotated[
        list[ScorerIdentifier],
        Evaluate.Include(unordered_when="sub_scorers_order_independent"),
        Param.Include(alias="scorers"),
    ] = Field(default_factory=list)
