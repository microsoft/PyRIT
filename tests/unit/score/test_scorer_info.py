# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pyrit.score as score_package
from pyrit.score import (
    AzureContentFilterScorer,
    SelfAskRefusalScorer,
    SubStringScorer,
    get_scorer_info,
)
from pyrit.score.scorer_info import _ScorerInfo


def test_get_scorer_info_returns_scorer_info_entries():
    infos = get_scorer_info()

    assert infos
    assert all(isinstance(info, _ScorerInfo) for info in infos)
    assert all(info.score_type in ("true_false", "float_scale") for info in infos)


def test_get_scorer_info_is_deterministic():
    assert get_scorer_info() == get_scorer_info()


def test_get_scorer_info_is_sorted():
    infos = get_scorer_info()
    keys = [(info.score_type, info.uses_llm, info.name) for info in infos]

    assert keys == sorted(keys)


def test_get_scorer_info_excludes_abstract_and_non_scorers():
    names = {info.name for info in get_scorer_info()}

    # Abstract bases / mixins and non-scorer exports must not appear.
    assert "Scorer" not in names
    assert "TrueFalseScorer" not in names
    assert "FloatScaleScorer" not in names
    assert "ConversationScorer" not in names
    assert "BatchScorer" not in names


def test_get_scorer_info_classifies_self_ask_as_true_false_llm():
    info = next(i for i in get_scorer_info() if i.name == SelfAskRefusalScorer.__name__)

    assert info.score_type == "true_false"
    assert info.uses_llm is True


def test_get_scorer_info_classifies_azure_content_filter_as_float_scale_non_llm():
    info = next(i for i in get_scorer_info() if i.name == AzureContentFilterScorer.__name__)

    assert info.score_type == "float_scale"
    assert info.uses_llm is False


def test_get_scorer_info_classifies_substring_as_true_false_non_llm():
    info = next(i for i in get_scorer_info() if i.name == SubStringScorer.__name__)

    assert info.score_type == "true_false"
    assert info.uses_llm is False


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
