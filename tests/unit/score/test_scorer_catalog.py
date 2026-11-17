# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from unittest.mock import patch

import pytest

from build_scripts.generate_scorer_capability_table import PAGE, main, render_page
from pyrit.score import (
    FloatScaleThresholdScorer,
    MessageToolCallScorer,
    PlagiarismScorer,
    Scorer,
    SubStringScorer,
    TrueFalseFallbackScorer,
    TrueFalseScorer,
)
from pyrit.score.scorer_catalog import (
    FLOAT_FEEDBACK_GUARANTEES,
    _overrides,
    describe_scorer_classes,
    public_scorer_classes,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _row(name: str):
    return next(row for row in describe_scorer_classes() if row.name == name)


def test_describing_the_classes_constructs_no_scorer():
    with patch.object(Scorer, "__init__", side_effect=AssertionError("a scorer was constructed")):
        rows = describe_scorer_classes()
    assert {row.name for row in rows} == {cls.__name__ for cls in public_scorer_classes()}


def test_the_published_page_is_current():
    assert (REPO_ROOT / PAGE).read_text(encoding="utf-8") == render_page()


def test_check_mode_reports_a_stale_page(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / PAGE.parent).mkdir(parents=True)
    (tmp_path / PAGE).write_text("stale", encoding="utf-8")
    assert main(["--check"]) == 1
    assert "out of date" in capsys.readouterr().out
    assert main([]) == 0
    assert main(["--check"]) == 0


def test_every_true_false_class_that_keeps_float_feedback_is_described():
    overriding = {
        cls.__name__
        for cls in public_scorer_classes()
        if issubclass(cls, TrueFalseScorer) and _overrides(cls, "_preserves_float_scale_value")
    }
    assert overriding == set(FLOAT_FEEDBACK_GUARANTEES)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "build",
    [
        lambda: SubStringScorer(substring="x"),
        lambda: MessageToolCallScorer(),
        lambda: FloatScaleThresholdScorer(scorer=PlagiarismScorer(reference_text="x"), threshold=0.5),
        lambda: TrueFalseFallbackScorer(
            scorer=FloatScaleThresholdScorer(scorer=PlagiarismScorer(reference_text="x"), threshold=0.5),
            fallback_scorer=FloatScaleThresholdScorer(scorer=PlagiarismScorer(reference_text="y"), threshold=0.5),
        ),
    ],
    ids=["leaf", "tool_calls", "threshold", "fallback_of_thresholds"],
)
def test_the_table_agrees_with_configured_scorers(build):
    scorer = build()
    capabilities = scorer.capabilities
    row = _row(type(scorer).__name__)

    assert row.result == "true/false"
    assert capabilities.preserves_float_scale_value is (row.float_feedback != "No")
    if type(scorer).SUPPORTED_SCORABLE_TYPES:
        assert row.evidence == ", ".join(sorted(t.__name__ for t in capabilities.scorable_types))
    if type(scorer).CONDITION_TYPE is not None:
        assert row.conditions == ", ".join(sorted(t.__name__ for t in capabilities.condition_types))
