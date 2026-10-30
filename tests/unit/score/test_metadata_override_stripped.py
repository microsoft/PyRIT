# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Judge-supplied original_float_value must not override the stored score.

normalize_score_to_float prefers an original_float_value metadata key over the
validated score_value. The threshold wrapper writes that key itself after
parsing; a raw float-scale scorer left the judge-supplied key authoritative,
so a judge body of score_value 0.0 + metadata original_float_value 1.0 stored
0.0 while every consumer computed 1.0.
"""

from pyrit.models import ComponentIdentifier
from pyrit.score.response_handler import _build_unvalidated_score

_SCORER = ComponentIdentifier(class_name="TestScorer", class_module=__name__)


def _build(metadata: dict):
    return _build_unvalidated_score(
        parsed_response={"score_value": "0.0", "rationale": "r", "metadata": metadata},
        score_value_output_key="score_value",
        rationale_output_key="rationale",
        description_output_key="description",
        metadata_output_key="metadata",
        category_output_key="category",
        scorer_identifier=_SCORER,
        scored_prompt_id="00000000-0000-0000-0000-000000000000",
        category=None,
        objective=None,
    )


def test_judge_supplied_original_float_value_is_stripped():
    score = _build({"original_float_value": "1.0"})
    assert score.score_metadata is not None
    assert "original_float_value" not in score.score_metadata


def test_out_of_range_override_is_stripped():
    score = _build({"original_float_value": 7.5})
    assert score.score_metadata is not None
    assert "original_float_value" not in score.score_metadata


def test_innocent_metadata_keys_are_retained():
    score = _build({"judge_confidence": 0.9, "notes": "ok"})
    assert score.score_metadata == {"judge_confidence": 0.9, "notes": "ok"}


def test_escaped_key_variant_is_not_consulted():
    score = _build({"original\\u005ffloat_value": "1.0"})
    # A different literal key: retained, and never consulted by
    # normalize_score_to_float, which reads the exact constant.
    assert "original_float_value" not in (score.score_metadata or {})
