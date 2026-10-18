# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from typing import Any

from pyrit.models import Score
from pyrit.output._derivation import resolve_scorer_name
from pyrit.output.base import PrinterBase
from pyrit.output.sink import Sink


class JsonScorePrinter(PrinterBase):
    """
    JSON printer for individual Score objects.

    Surfaces the same fields the pretty score printer shows — scorer, type, value,
    category, rationale — plus the objective, as structured data. ``build`` returns
    the dict so other printers (e.g. the conversation printer) can embed a score;
    ``render_async`` serializes a list of scores.
    """

    def __init__(self, *, sink: Sink | None = None, indent: int = 2) -> None:
        """
        Initialize the JSON score printer.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            indent (int): JSON indentation width. Defaults to 2.
        """
        super().__init__(sink=sink)
        self._indent = indent

    def build(self, score: Score) -> dict[str, Any]:
        """
        Build the curated JSON representation of a single score.

        Args:
            score (Score): The score to serialize.

        Returns:
            dict[str, Any]: The score's scorer, type, value, category, rationale, and objective.
        """
        scorer = resolve_scorer_name(score)
        return {
            "scorer": scorer,
            "score_type": score.score_type,
            "score_value": score.score_value,
            "score_category": score.score_category,
            "score_rationale": score.score_rationale,
            "objective": score.objective,
        }

    async def render_async(self, scores: list[Score]) -> str:
        """
        Render a list of scores as a JSON string.

        Args:
            scores (list[Score]): The scores to render.

        Returns:
            str: The scores serialized as indented JSON.
        """
        payload = [self.build(score) for score in scores]
        return json.dumps(payload, indent=self._indent, default=str, ensure_ascii=False)
