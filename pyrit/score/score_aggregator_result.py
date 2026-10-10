# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScoreAggregatorResult:
    """
    Common result object returned by score aggregators.

    Attributes:
        value (bool | float | None): The aggregated value. For true/false aggregators this is
            a boolean. For float-scale aggregators, this is a float in the range [0, 1].
            ``None`` means no verdict was reachable, because at least one constituent score
            was undetermined and the aggregation could not settle without it.
        description (str): A short, human-friendly description of the aggregation outcome.
        rationale (str): Combined rationale from constituent scores.
        category (list[str]): Combined list of categories from constituent scores.
        metadata (dict[str, str | int | float]): Combined metadata from constituent scores.
    """

    value: bool | float | None
    description: str
    rationale: str
    category: list[str]
    metadata: dict[str, str | int | float]
