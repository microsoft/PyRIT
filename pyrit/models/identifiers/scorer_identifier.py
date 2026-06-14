# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Strongly-typed projection of a scorer's identifier."""

from __future__ import annotations

from pydantic import Field

from pyrit.models.identifiers.component_identifier import ComponentIdentifier
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
    scorer_type: str | None = None
    #: Name of the aggregator function combining sub-scores (e.g., ``"AND_"``).
    score_aggregator: str | None = None
    #: Target an LLM-backed scorer calls (e.g., ``SelfAskScaleScorer``).
    prompt_target: TargetIdentifier | None = None
    #: Nested scorers a composite wraps, typed recursively.
    sub_scorers: list[ScorerIdentifier] = Field(default_factory=list)
