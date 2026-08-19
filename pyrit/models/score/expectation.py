# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass, field

from pyrit.models.score.condition import Condition  # noqa: TC001  (runtime-required by dataclass field annotations)


@dataclass(frozen=True, kw_only=True)
class ScoringExpectation:
    """
    What a scorer scores against.

    An expectation is a single parameter, so a question authored in a technique
    configuration or a seed can reach a scorer through an attack that knows nothing
    about it. It has two independent axes.

    ``objective`` carries the intent: prose describing what the run is trying to do.
    Components read it for framing — an adversarial target renders it into a system
    prompt, a report prints it — and none of them match it.

    ``conditions`` carry the criteria: typed objects routed by type to the scorers that
    match them. Attacks forward them without inspecting them, and a scorer matches at
    most one of them.
    """

    objective: str | None = None
    conditions: tuple[Condition, ...] = field(default_factory=tuple)
