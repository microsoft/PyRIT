# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

import pyrit.score as score_package
from pyrit.models import ScorerCapabilityName
from pyrit.score import (
    AzureContentFilterScorer,
    FloatScaleFallbackScorer,
    FloatScaleThresholdScorer,
    MessageToolCallScorer,
    PlagiarismScorer,
    Scorer,
    SelfAskRefusalScorer,
    SubStringScorer,
    TrueFalseFallbackScorer,
    TrueFalseScorer,
    get_scorer_info,
)
from pyrit.score.scorer_info import (
    _FLOAT_FEEDBACK_GUARANTEES,
    _overrides,
    _public_scorer_classes,
    _ScorerInfo,
)

NON_MESSAGE_SCORERS = {
    "AssertJudgeScorer",
    "FileWriteScorer",
    "FloatScaleFallbackScorer",
    "FloatScaleThresholdScorer",
    "ManualScorer",
    "MessageToolCallScorer",
    "OtelToolCallOrderScorer",
    "OtelToolCallScorer",
    "ProgramExecutionScorer",
    "TrueFalseCompositeScorer",
    "TrueFalseFallbackScorer",
    "TrueFalseInverterScorer",
    "TrueFalseScoreSelector",
}


def _info(name: str) -> _ScorerInfo:
    return next(info for info in get_scorer_info() if info.name == name)


def test_get_scorer_info_returns_scorer_info_entries():
    infos = get_scorer_info()

    assert infos
    assert all(isinstance(info, _ScorerInfo) for info in infos)
    assert all(info.score_type in ("true_false", "float_scale") for info in infos)


def test_get_scorer_info_is_deterministic():
    assert get_scorer_info() == get_scorer_info()


def test_get_scorer_info_is_sorted():
    infos = get_scorer_info()
    keys = [(info.score_type, info.uses_llm is None, info.uses_llm is True, info.name) for info in infos]

    assert keys == sorted(keys)


def test_get_scorer_info_excludes_abstract_and_non_scorers():
    names = {info.name for info in get_scorer_info()}

    # Abstract bases / mixins and non-scorer exports must not appear.
    assert "Scorer" not in names
    assert "TrueFalseScorer" not in names
    assert "FloatScaleScorer" not in names
    assert "ConversationScorer" not in names
    assert "BatchScorer" not in names


def test_get_scorer_info_includes_scorers_outside_the_message_families():
    names = {info.name for info in get_scorer_info()}

    assert names >= NON_MESSAGE_SCORERS


def test_get_scorer_info_classifies_self_ask_as_true_false_llm():
    info = _info(SelfAskRefusalScorer.__name__)

    assert info.score_type == "true_false"
    assert info.uses_llm is True


def test_get_scorer_info_classifies_azure_content_filter_as_float_scale_non_llm():
    info = _info(AzureContentFilterScorer.__name__)

    assert info.score_type == "float_scale"
    assert info.uses_llm is False
    assert info.float_feedback == "not applicable"


def test_get_scorer_info_classifies_substring_as_true_false_non_llm():
    info = _info(SubStringScorer.__name__)

    assert info.score_type == "true_false"
    assert info.uses_llm is False


def test_get_scorer_info_defers_llm_use_of_wrappers_to_their_scorers():
    info = _info(FloatScaleThresholdScorer.__name__)

    assert info.uses_llm is None
    assert info.evidence == "same as wrapped"
    assert info.conditions == "same as wrapped"


def test_get_scorer_info_skips_mocked_exports():
    # Another test in the suite may patch a pyrit.score export with an autospec/spec=type
    # mock, which reports isinstance(obj, type) as True but makes issubclass raise TypeError.
    # get_scorer_info must skip such entries rather than blow up.
    fake = MagicMock(spec=type)
    with patch.object(score_package, "SubStringScorer", fake):
        infos = get_scorer_info()

    names = {info.name for info in infos}
    assert "SubStringScorer" not in names
    assert infos  # other scorers are still returned


def test_get_scorer_info_constructs_no_scorer():
    with patch.object(Scorer, "__init__", side_effect=AssertionError("a scorer was constructed")):
        infos = get_scorer_info()
    assert {info.name for info in infos} == {cls.__name__ for cls in _public_scorer_classes()}


def test_every_true_false_class_that_keeps_float_feedback_is_described():
    overriding = {
        cls.__name__
        for cls in _public_scorer_classes()
        if issubclass(cls, TrueFalseScorer) and _overrides(cls, name="_preserves_float_scale_value")
    }
    assert overriding == set(_FLOAT_FEEDBACK_GUARANTEES)


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
def test_get_scorer_info_agrees_with_configured_scorers(build):
    scorer = build()
    capabilities = scorer.capabilities
    info = _info(type(scorer).__name__)

    assert info.score_type == "true_false"
    keeps_float_feedback = capabilities.includes(capability=ScorerCapabilityName.PRESERVES_FLOAT_SCALE_VALUE)
    assert keeps_float_feedback is (info.float_feedback != "no")
    if type(scorer).SUPPORTED_SCORABLE_TYPES:
        evidence = sorted(t.__name__.removesuffix("Scorable") for t in capabilities.scorable_types)
        assert info.evidence == ", ".join(evidence)
    if type(scorer).CONDITION_TYPE is not None:
        assert info.conditions == ", ".join(sorted(t.__name__ for t in capabilities.condition_types))


@pytest.mark.usefixtures("patch_central_database")
def test_get_scorer_info_float_fallback_keeps_no_float_feedback():
    # FloatScaleFallbackScorer inherits the fallback's float feedback hook, but float results never promise it.
    scorer = FloatScaleFallbackScorer(
        scorer=PlagiarismScorer(reference_text="x"), fallback_scorer=PlagiarismScorer(reference_text="y")
    )
    info = _info(FloatScaleFallbackScorer.__name__)

    assert info.score_type == "float_scale"
    assert info.float_feedback == "not applicable"
    assert not scorer.capabilities.includes(capability=ScorerCapabilityName.PRESERVES_FLOAT_SCALE_VALUE)
