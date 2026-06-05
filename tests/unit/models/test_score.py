# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
import warnings
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from pyrit.models import ComponentIdentifier, Score
from pyrit.models.score import UnvalidatedScore


def _make_score(**overrides) -> Score:
    defaults: dict = {
        "score_value": "false",
        "score_value_description": "true false score",
        "score_type": "true_false",
        "score_rationale": "Rationale text",
        "scorer_class_identifier": ComponentIdentifier(class_name="TestScorer", class_module="pyrit.score"),
        "message_piece_id": str(uuid.uuid4()),
    }
    defaults.update(overrides)
    return Score(**defaults)


# --------------------------------------------------------------------------- #
# Defaults / field behavior
# --------------------------------------------------------------------------- #
def test_defaults_populated():
    score = _make_score()
    assert isinstance(score.id, uuid.UUID)
    assert score.timestamp.tzinfo is not None
    assert score.score_metadata == {}
    assert score.score_category is None
    assert score.objective is None


def test_score_metadata_none_coerced_to_empty_dict():
    score = _make_score(score_metadata=None)
    assert score.score_metadata == {}


def test_aware_timestamp_is_preserved():
    aware = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    score = _make_score(timestamp=aware)
    assert score.timestamp == aware


def test_naive_timestamp_is_rejected():
    naive = datetime(2026, 1, 15, 12, 0, 0)  # noqa: DTZ001
    with pytest.raises(ValidationError):
        _make_score(timestamp=naive)


def test_extra_kwarg_is_forbidden():
    with pytest.raises(ValidationError):
        _make_score(not_a_real_field="x")


def test_scorer_class_identifier_defaults_to_none():
    score = _make_score(scorer_class_identifier=None)
    assert score.scorer_class_identifier is None
    # __str__ must not blow up when the identifier is absent
    assert "false" in str(score)


# --------------------------------------------------------------------------- #
# Validators
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["true", "false", "TRUE", "False"])
def test_true_false_accepts_valid_values(value):
    score = _make_score(score_type="true_false", score_value=value)
    assert score.score_value == value


def test_true_false_rejects_invalid_value():
    with pytest.raises(ValidationError):
        _make_score(score_type="true_false", score_value="maybe")


@pytest.mark.parametrize("value", ["0", "0.5", "1", "1.0"])
def test_float_scale_accepts_in_range_values(value):
    score = _make_score(score_type="float_scale", score_value=value)
    assert score.score_value == value


def test_float_scale_rejects_out_of_range():
    with pytest.raises(ValidationError):
        _make_score(score_type="float_scale", score_value="1.5")


def test_float_scale_rejects_non_numeric():
    with pytest.raises(ValidationError):
        _make_score(score_type="float_scale", score_value="abc")


def test_unknown_type_skips_value_validation():
    score = _make_score(score_type="unknown", score_value="anything")
    assert score.score_value == "anything"


def test_assignment_is_not_revalidated():
    # validate_assignment=False — scorers mutate score_value after construction
    # (e.g. true_false_inverter_scorer / float_scale_threshold_scorer).
    score = _make_score(score_type="true_false", score_value="true")
    score.score_value = "maybe"
    assert score.score_value == "maybe"


# --------------------------------------------------------------------------- #
# get_value / __str__
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value,expected",
    [("true", True), ("True", True), ("false", False), ("FALSE", False)],
)
def test_get_value_true_false(value, expected):
    assert _make_score(score_type="true_false", score_value=value).get_value() is expected


def test_get_value_float_scale():
    assert _make_score(score_type="float_scale", score_value="0.25").get_value() == 0.25


def test_get_value_unknown_raises():
    with pytest.raises(ValueError):
        _make_score(score_type="unknown", score_value="x").get_value()


def test_str_includes_scorer_class_name_and_category():
    score = _make_score(score_category=["violence", "hate"])
    rendered = str(score)
    assert "TestScorer" in rendered
    assert "violence, hate" in rendered
    assert rendered == repr(score)


# --------------------------------------------------------------------------- #
# Serialization round-trip (model_dump / model_validate)
# --------------------------------------------------------------------------- #
def test_model_dump_contains_expected_keys():
    score = _make_score(
        id=str(uuid.uuid4()),
        score_category=["Category1"],
        score_metadata={"key": "value"},
        timestamp=datetime.now(tz=timezone.utc),
        objective="Task1",
    )
    result = score.model_dump(mode="json")
    expected_keys = {
        "id",
        "score_value",
        "score_value_description",
        "score_type",
        "score_category",
        "score_rationale",
        "score_metadata",
        "scorer_class_identifier",
        "message_piece_id",
        "timestamp",
        "objective",
    }
    assert expected_keys <= set(result)
    assert result["id"] == str(score.id)
    assert result["message_piece_id"] == str(score.message_piece_id)
    assert result["scorer_class_identifier"] == score.scorer_class_identifier.to_dict()
    assert result["objective"] == "Task1"


def test_model_validate_roundtrip():
    original = _make_score(
        score_type="true_false",
        score_value="true",
        score_category=["violence", "hate"],
        score_metadata={"confidence": 0.95, "model": "gpt-4"},
        scorer_class_identifier=ComponentIdentifier(
            class_name="SelfAskTrueFalseScorer",
            class_module="pyrit.score",
            params={"system_prompt": "Rate the response"},
        ),
        message_piece_id=str(uuid.uuid4()),
        timestamp=datetime.now(tz=timezone.utc),
        objective="Generate a violent response",
    )
    roundtripped = Score.model_validate(original.model_dump(mode="json"))
    assert original.model_dump(mode="json") == roundtripped.model_dump(mode="json")


def test_model_validate_accepts_dict_scorer_identifier():
    original = _make_score()
    dumped = original.model_dump(mode="json")
    assert isinstance(dumped["scorer_class_identifier"], dict)
    reconstructed = Score.model_validate(dumped)
    assert isinstance(reconstructed.scorer_class_identifier, ComponentIdentifier)


# --------------------------------------------------------------------------- #
# Deprecated method shims (removed in 0.16.0)
# --------------------------------------------------------------------------- #
def test_to_dict_emits_warning_and_matches_model_dump():
    score = _make_score()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = score.to_dict()
    msgs = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("to_dict" in str(m.message) for m in msgs)
    assert result == score.model_dump(mode="json")


def test_from_dict_emits_warning_and_matches_model_validate():
    score = _make_score()
    serialized = score.model_dump(mode="json")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        reconstructed = Score.from_dict(serialized)
    msgs = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("from_dict" in str(m.message) for m in msgs)
    assert reconstructed.model_dump(mode="json") == serialized


def test_validate_emits_warning_and_revalidates():
    score = _make_score(score_type="true_false", score_value="true")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        score.validate()
    msgs = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("validate" in str(m.message) for m in msgs)


def test_validate_raises_when_instance_made_invalid():
    score = _make_score(score_type="true_false", score_value="true")
    score.score_value = "maybe"
    with pytest.raises(ValueError):
        score.validate()


# --------------------------------------------------------------------------- #
# UnvalidatedScore
# --------------------------------------------------------------------------- #
def test_unvalidated_score_to_score():
    scorer_id = ComponentIdentifier(class_name="LikertScorer", class_module="pyrit.score")
    unvalidated = UnvalidatedScore(
        raw_score_value="3",
        score_value_description="middle",
        score_category=["hate"],
        score_rationale="because",
        score_metadata={"likert_value": 3},
        scorer_class_identifier=scorer_id,
        message_piece_id=str(uuid.uuid4()),
        objective="obj",
    )
    score = unvalidated.to_score(score_value="0.5", score_type="float_scale")
    assert score.score_value == "0.5"
    assert score.score_type == "float_scale"
    assert score.score_category == ["hate"]
    assert score.objective == "obj"
