# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from typing import Any

import pytest

from pyrit.models import (
    AssertCategoryJudgment,
    AssertDimensionResult,
    AssertNodeJudgment,
    AssertSampleVote,
    AssertSampleVotes,
    AssertSkipReason,
)

_NODE = AssertNodeJudgment(node_name="private_disclosure", violated=True, confidence="high", reasoning="Shared it.")


@pytest.mark.parametrize(
    "value",
    [
        AssertDimensionResult(dimension="overrefusal", skip_reason=AssertSkipReason.TARGET_ERROR),
        AssertDimensionResult(dimension="quality", not_applicable=True, scale=(1, 2, 3)),
        AssertDimensionResult(grade="hedged", scale=("none", "hedged", "stated")),
        AssertCategoryJudgment(
            node_judgments=(_NODE,),
            original_score_value=False,
            correction_reason="Corrected.",
            original_description="Original.",
            warnings=("duplicate_node_name:private_disclosure",),
        ),
        AssertCategoryJudgment(node_judgments=(), original_score_value=True),
        AssertSampleVotes(
            votes=(AssertSampleVote.TRUE, AssertSampleVote.NOT_APPLICABLE, AssertSampleVote.UNDETERMINED),
            agreement=1.0,
            voted_score_value=False,
        ),
    ],
)
def test_score_metadata_round_trips(value: Any) -> None:
    metadata = value.to_metadata()
    assert all(isinstance(item, (str, int, float)) for item in metadata.values())
    assert type(value).from_metadata(metadata=metadata) == value


def test_existing_score_metadata_format_is_kept() -> None:
    assert AssertDimensionResult(not_applicable=True, scale=(1, 2)).to_metadata() == {
        "assert_not_applicable": "true",
        "assert_scale": "[1, 2]",
    }
    assert AssertCategoryJudgment(node_judgments=(_NODE,), original_score_value=True).to_metadata() == {
        "assert_judgment_mode": "category_validated",
        "assert_node_judgments": json.dumps([_NODE.model_dump(mode="json")], ensure_ascii=True),
        "assert_original_score_value": "true",
    }
    votes = (AssertSampleVote.TRUE, AssertSampleVote.FALSE)
    assert AssertSampleVotes(votes=votes, agreement=0.5).to_metadata() == {
        "assert_sample_count": 2,
        "assert_sample_votes": '["true", "false"]',
        "assert_sample_agreement": 0.5,
    }


@pytest.mark.parametrize("model", [AssertDimensionResult, AssertCategoryJudgment, AssertSampleVotes])
@pytest.mark.parametrize("metadata", [None, {}, {"metadata": "Judge evidence.", "original_float_value": 1.0}])
def test_absent_keys_are_unset(model: Any, metadata: dict[str, Any] | None) -> None:
    assert model.from_metadata(metadata=metadata) is None


@pytest.mark.parametrize(
    ("model", "metadata"),
    [
        (AssertDimensionResult, {"assert_scoring_skipped": "judge_refused"}),
        (AssertDimensionResult, {"assert_not_applicable": "yes"}),
        (AssertDimensionResult, {"assert_grade": "not json", "assert_scale": "[1, 2]"}),
        (AssertDimensionResult, {"assert_grade": "3", "assert_scale": "[1, 2]"}),
        (AssertDimensionResult, {"assert_grade": "1"}),
        (AssertDimensionResult, {"assert_scale": '[1, "two"]'}),
        (AssertDimensionResult, {"assert_dimension": 3}),
        (AssertCategoryJudgment, {"assert_node_judgments": "[]", "assert_original_score_value": "true"}),
        (
            AssertCategoryJudgment,
            {
                "assert_judgment_mode": "category_validated",
                "assert_node_judgments": '[{"node_name": "a", "violated": "yes"}]',
                "assert_original_score_value": "true",
            },
        ),
        (
            AssertCategoryJudgment,
            {
                "assert_judgment_mode": "category_validated",
                "assert_node_judgments": "[]",
                "assert_original_score_value": "true",
                "assert_correction_reason": "Corrected.",
            },
        ),
        (AssertSampleVotes, {"assert_sample_count": 3, "assert_sample_votes": '["true", "false"]'}),
        (AssertSampleVotes, {"assert_sample_count": 2, "assert_sample_votes": '["true", "maybe"]'}),
        (AssertSampleVotes, {"assert_sample_agreement": 0.5}),
    ],
)
def test_malformed_present_keys_raise(model: Any, metadata: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        model.from_metadata(metadata=metadata)


def test_merge_into_replaces_only_its_own_keys() -> None:
    metadata = {"metadata": "Judge evidence.", "assert_not_applicable": "true", "assert_sample_count": 2}
    assert AssertDimensionResult(dimension="quality").merge_into(metadata) == {
        "metadata": "Judge evidence.",
        "assert_sample_count": 2,
        "assert_dimension": "quality",
    }
