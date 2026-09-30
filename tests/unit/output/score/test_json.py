# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

from pyrit.models import ComponentIdentifier, Score
from pyrit.output.score.json import JsonScorePrinter


def _score(*, value: str = "true", score_type: str = "true_false") -> Score:
    return Score(
        score_type=score_type,
        score_value=value,
        score_category=["hate"],
        score_rationale="because reasons",
        objective="make it fail",
        scorer_class_identifier=ComponentIdentifier(class_name="MockScorer", class_module="tests"),
    )


def test_build_surfaces_curated_fields():
    printer = JsonScorePrinter()

    data = printer.build(_score(value="0.42", score_type="float_scale"))

    assert data == {
        "scorer": "MockScorer",
        "score_type": "float_scale",
        "score_value": "0.42",
        "score_category": ["hate"],
        "score_rationale": "because reasons",
        "objective": "make it fail",
    }


def test_build_scorer_none_when_no_identifier():
    printer = JsonScorePrinter()
    score = _score()
    score.scorer_class_identifier = None

    assert printer.build(score)["scorer"] is None


async def test_render_async_serializes_list():
    printer = JsonScorePrinter()

    rendered = await printer.render_async([_score(), _score(value="false")])
    payload = json.loads(rendered)

    assert isinstance(payload, list)
    assert len(payload) == 2
    assert payload[0]["score_value"] == "true"
    assert payload[1]["score_value"] == "false"
