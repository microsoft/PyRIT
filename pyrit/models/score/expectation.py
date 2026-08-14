# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoringExpectation:
    """
    What a scorer scores against.

    An expectation is a single parameter that attacks forward without inspecting it,
    so a question authored in a technique configuration or a seed can reach a scorer
    through an attack that knows nothing about it.
    """

    objective: str | None = None
