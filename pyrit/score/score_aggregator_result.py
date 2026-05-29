# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True, slots=True)
class ScoreAggregatorResult:
    """
    Common result object returned by score aggregators.

    Attributes:
        value (Union[bool, float]): The aggregated value. For true/false aggregators this is
            a boolean. For float-scale aggregators, this is a float in the range [0, 1].
        description (str): A short, human-friendly description of the aggregation outcome.
        rationale (str): Combined rationale from constituent scores.
        category (List[str]): Combined list of categories from constituent scores.
        metadata (Dict[str, Union[str, int, float]]): Combined metadata from constituent scores.
    """

    value: Union[bool, float]
    description: str
    rationale: str
    category: list[str]
    metadata: dict[str, Union[str, int, float]]
