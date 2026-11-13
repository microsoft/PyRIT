# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.models import (
    ComponentIdentifier,
    CyberSecEvalMitreVerdict,
    InvalidCyberSecEvalMitreVerdictError,
    Score,
)


def _score(metadata: dict | None) -> Score:
    return Score(
        score_type="true_false",
        score_value="false",
        score_category=None,
        score_value_description="",
        score_rationale="",
        score_metadata=metadata,
        scorer_class_identifier=ComponentIdentifier(class_name="CyberSecEvalMitreScorer", class_module="test"),
    )


def test_key_is_namespaced():
    assert CyberSecEvalMitreVerdict.KEY == "cyberseceval_mitre_verdict"


@pytest.mark.parametrize("verdict", ["refusal", "malicious", "benign", "else"])
def test_round_trips_through_score_metadata(verdict):
    recorded = CyberSecEvalMitreVerdict(verdict=verdict)

    assert recorded.to_metadata() == {"cyberseceval_mitre_verdict": verdict}
    assert CyberSecEvalMitreVerdict.read(_score({**recorded.to_metadata(), "other": "x"})) == recorded


@pytest.mark.parametrize(
    "metadata",
    [None, {}, {"other": "x"}, {CyberSecEvalMitreVerdict.KEY: "Malicious"}, {CyberSecEvalMitreVerdict.KEY: 1}],
)
def test_missing_or_invalid_verdicts_read_as_unset(metadata):
    assert CyberSecEvalMitreVerdict.from_metadata(metadata=metadata) is None
    assert CyberSecEvalMitreVerdict.read(_score(metadata)) is None


def test_reading_none_score_is_unset():
    assert CyberSecEvalMitreVerdict.read(None) is None


@pytest.mark.parametrize("verdict", ["", "unknown", "BENIGN"])
def test_construction_rejects_verdicts_cyberseceval_does_not_report(verdict):
    with pytest.raises(InvalidCyberSecEvalMitreVerdictError):
        CyberSecEvalMitreVerdict(verdict=verdict)
