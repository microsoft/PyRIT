# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Strongly-typed projection of a scorer's identifier."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from pyrit.models.identifiers.component_identifier import ComponentIdentifier
from pyrit.models.identifiers.evaluation_markers import Evaluate
from pyrit.models.identifiers.target_identifier import (  # noqa: TC001
    TargetIdentifier,  # runtime-required by Pydantic field annotations
)


class ScorerIdentifier(ComponentIdentifier):
    """
    Strongly-typed projection of a ``Scorer``'s ``ComponentIdentifier``.

    Promotes the ``scorer_type`` discriminator, the ``score_aggregator`` name, and
    the scorer's own child slots — ``prompt_target`` (an LLM target) and
    ``sub_scorers`` (nested scorers).
    """

    #: The scorer category (e.g., ``"true_false"`` or ``"float_scale"``).
    scorer_type: Annotated[str | None, Evaluate.Include()] = None
    #: Name of the aggregator function combining sub-scores (e.g., ``"AND_"``).
    score_aggregator: Annotated[str | None, Evaluate.Include()] = None
    #: Target an LLM-backed scorer calls (e.g., ``SelfAskScaleScorer``).
    prompt_target: Annotated[TargetIdentifier | None, Evaluate.Include()] = None
    #: Nested scorers a composite wraps, typed recursively.
    sub_scorers: Annotated[list[ScorerIdentifier], Evaluate.Include()] = Field(default_factory=list)
