# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from collections.abc import Sequence
from functools import partial
from typing import Any

import pytest

from pyrit.exceptions import InvalidJsonException
from pyrit.models import ComponentIdentifier
from pyrit.score import (
    CallableResponseHandler,
    CategoryConflictPolicy,
    JsonSchemaResponseHandler,
)
from pyrit.score.response_handler import (
    NumericRangeResponseHandler,
    TrueFalseResponseHandler,
)

SCORER_IDENTIFIER = ComponentIdentifier(class_name="TestScorer", class_module=__name__)


@pytest.mark.parametrize("response_text", ["[]", '["score"]', '"true"', "1", "true", "null"])
def test_json_schema_response_handler_rejects_non_object_response(response_text: str) -> None:
    handler = JsonSchemaResponseHandler()

    with pytest.raises(InvalidJsonException, match="expected a top-level object"):
        handler.parse(
            response_text=response_text,
            scorer_identifier=SCORER_IDENTIFIER,
            scored_prompt_id="test-id",
        )


@pytest.mark.parametrize("score_value", ["nan", "NaN", "inf", "-inf", "Infinity"])
def test_json_schema_response_handler_rejects_non_finite_numeric_values(score_value: str) -> None:
    handler = JsonSchemaResponseHandler(numeric_value=True)

    with pytest.raises(InvalidJsonException, match="finite float"):
        handler.parse(
            response_text=f'{{"score_value": "{score_value}", "rationale": "test"}}',
            scorer_identifier=SCORER_IDENTIFIER,
            scored_prompt_id="test-id",
        )


@pytest.mark.parametrize(
    ("json_value", "expected"),
    [
        ("true", "true"),
        ("false", "false"),
        ('"true"', "true"),
        ('"false"', "false"),
    ],
)
def test_true_false_response_handler_accepts_boolean_values(json_value: str, expected: str) -> None:
    handler = TrueFalseResponseHandler(response_handler=JsonSchemaResponseHandler())

    score = handler.parse(
        response_text=f'{{"score_value": {json_value}, "rationale": "test"}}',
        scorer_identifier=SCORER_IDENTIFIER,
        scored_prompt_id="test-id",
    )

    assert score.raw_score_value == expected


@pytest.mark.parametrize(
    ("json_value", "expected"),
    [
        ('"true "', "true"),
        ('" false"', "false"),
        ('"True\\n"', "true"),
        ('"  FALSE  "', "false"),
    ],
)
def test_true_false_response_handler_strips_whitespace_around_verdict(json_value: str, expected: str) -> None:
    # A judge returning a valid verdict with incidental surrounding whitespace
    # (e.g. a trailing newline) must not be rejected as out-of-domain.
    handler = TrueFalseResponseHandler(response_handler=JsonSchemaResponseHandler())

    score = handler.parse(
        response_text=f'{{"score_value": {json_value}, "rationale": "test"}}',
        scorer_identifier=SCORER_IDENTIFIER,
        scored_prompt_id="test-id",
    )

    assert score.raw_score_value == expected


def test_true_false_response_handler_rejects_value_outside_domain() -> None:
    handler = TrueFalseResponseHandler(response_handler=JsonSchemaResponseHandler())

    with pytest.raises(InvalidJsonException, match="must be 'true' or 'false'"):
        handler.parse(
            response_text='{"score_value": "refusal", "rationale": "test"}',
            scorer_identifier=SCORER_IDENTIFIER,
            scored_prompt_id="test-id",
        )


@pytest.mark.parametrize("response_category", ["harm", "violence"])
def test_json_schema_response_handler_rejects_category_in_both_by_default(response_category: str) -> None:
    handler = JsonSchemaResponseHandler()

    with pytest.raises(ValueError, match="Category is present in the response and an argument"):
        handler.parse(
            response_text=json.dumps({"score_value": "1", "rationale": "r", "category": response_category}),
            scorer_identifier=SCORER_IDENTIFIER,
            scored_prompt_id="test-id",
            category="harm",
        )


def test_json_schema_response_handler_prefer_response_category() -> None:
    handler = JsonSchemaResponseHandler(category_conflict_policy=CategoryConflictPolicy.PREFER_RESPONSE)

    from_response = handler.parse(
        response_text='{"score_value": "1", "rationale": "r", "category": "violence"}',
        scorer_identifier=SCORER_IDENTIFIER,
        scored_prompt_id="test-id",
        category="harm",
    )
    fallback = handler.parse(
        response_text='{"score_value": "1", "rationale": "r"}',
        scorer_identifier=SCORER_IDENTIFIER,
        scored_prompt_id="test-id",
        category="harm",
    )

    assert from_response.score_category == ["violence"]
    assert fallback.score_category == ["harm"]


@pytest.mark.parametrize("policy", ["unsupported", None, True])
def test_json_schema_response_handler_rejects_invalid_category_conflict_policy(policy: Any) -> None:
    with pytest.raises(ValueError, match="CategoryConflictPolicy"):
        JsonSchemaResponseHandler(category_conflict_policy=policy)


@pytest.mark.parametrize("policy", list(CategoryConflictPolicy))
@pytest.mark.parametrize(
    ("response_category", "configured_category", "expected_category"),
    [("violence", None, ["violence"]), (None, "harm", ["harm"]), (None, None, None)],
)
def test_json_schema_response_handler_category_policy_without_conflict(
    policy: CategoryConflictPolicy,
    response_category: str | None,
    configured_category: str | None,
    expected_category: list[str] | None,
) -> None:
    handler = JsonSchemaResponseHandler(category_conflict_policy=policy)

    score = handler.parse(
        response_text=json.dumps({"score_value": "1", "rationale": "r", "category": response_category}),
        scorer_identifier=SCORER_IDENTIFIER,
        scored_prompt_id="test-id",
        category=configured_category,
    )

    assert score.score_category == expected_category


@pytest.mark.parametrize("category_output_key", ["category", "label"])
@pytest.mark.parametrize(
    ("response_category", "configured_category", "expected_category"),
    [
        ("violence", "harm", ["harm"]),
        ("harm", "harm", ["harm"]),
        (["violence", "privacy"], "harm", ["harm"]),
        (None, "harm", ["harm"]),
        ("violence", None, ["violence"]),
        (["violence", "privacy"], None, ["violence", "privacy"]),
        (None, None, None),
        ("violence", ["harm", "abuse"], ["harm", "abuse"]),
        ("violence", [], []),
        ("violence", "", [""]),
        (123, "harm", ["harm"]),
    ],
)
def test_json_schema_response_handler_prefers_configured_category(
    category_output_key: str,
    response_category: object,
    configured_category: Sequence[str] | str | None,
    expected_category: list[str] | None,
) -> None:
    handler = JsonSchemaResponseHandler(
        category_output_key=category_output_key,
        category_conflict_policy=CategoryConflictPolicy.PREFER_CONFIGURED,
    )

    score = handler.parse(
        response_text=json.dumps({"score_value": "1", "rationale": "r", category_output_key: response_category}),
        scorer_identifier=SCORER_IDENTIFIER,
        scored_prompt_id="test-id",
        category=configured_category,
    )

    assert score.score_category == expected_category


@pytest.mark.parametrize("response_category", [123, {"label": "harm"}, [123]])
def test_json_schema_response_handler_validates_response_category_without_configuration(
    response_category: object,
) -> None:
    handler = JsonSchemaResponseHandler(category_conflict_policy=CategoryConflictPolicy.PREFER_CONFIGURED)

    with pytest.raises(InvalidJsonException, match="'category' must be"):
        handler.parse(
            response_text=json.dumps({"score_value": "1", "rationale": "r", "category": response_category}),
            scorer_identifier=SCORER_IDENTIFIER,
            scored_prompt_id="test-id",
        )


def test_json_schema_response_handler_replay_identifier_unchanged_by_default() -> None:
    default = JsonSchemaResponseHandler()._get_replay_identifier()
    rejecting = JsonSchemaResponseHandler(
        category_conflict_policy=CategoryConflictPolicy.REJECT_CONFLICT
    )._get_replay_identifier()
    response_first = JsonSchemaResponseHandler(
        category_conflict_policy=CategoryConflictPolicy.PREFER_RESPONSE
    )._get_replay_identifier()
    configured_first = JsonSchemaResponseHandler(
        category_conflict_policy=CategoryConflictPolicy.PREFER_CONFIGURED
    )._get_replay_identifier()

    assert default is not None
    assert rejecting == default
    assert "category_conflict_policy" not in default
    assert response_first == {**default, "category_conflict_policy": "prefer_response"}
    assert configured_first == {**default, "category_conflict_policy": "prefer_configured"}


@pytest.mark.parametrize(
    ("policy", "legacy_preference_key"),
    [
        (CategoryConflictPolicy.PREFER_CONFIGURED, "prefer_configured_category"),
        (CategoryConflictPolicy.PREFER_RESPONSE, "prefer_response_category"),
    ],
)
@pytest.mark.parametrize(
    ("response_text", "configured_category", "strict_compatible"),
    [
        ('{"category": "violence"}', None, True),
        ("{}", "harm", True),
        ('{"category": null}', "harm", True),
        ('{"category": "violence"}', "harm", False),
        ('{"category": "harm"}', "harm", False),
        ('{"category": []}', "harm", False),
        ("[]", "harm", False),
        ("invalid json", "harm", False),
    ],
)
def test_json_schema_response_handler_legacy_contract_requires_unchanged_category_parsing(
    policy: CategoryConflictPolicy,
    legacy_preference_key: str,
    response_text: str,
    configured_category: str | None,
    strict_compatible: bool,
) -> None:
    handler = JsonSchemaResponseHandler(category_conflict_policy=policy)

    legacy = handler._legacy_replay_identifiers(response_text=response_text, category=configured_category)

    historical = JsonSchemaResponseHandler()._get_replay_identifier()
    assert historical is not None
    expected = [{**historical, legacy_preference_key: True}]
    if strict_compatible:
        expected.append(historical)
    assert legacy == expected


@pytest.mark.parametrize("score_value", ["1", "5.5", "10"])
def test_numeric_range_response_handler_accepts_values_within_range(score_value: str) -> None:
    handler = NumericRangeResponseHandler(
        response_handler=JsonSchemaResponseHandler(numeric_value=True), minimum_value=1, maximum_value=10
    )

    score = handler.parse(
        response_text=f'{{"score_value": "{score_value}", "rationale": "test"}}',
        scorer_identifier=SCORER_IDENTIFIER,
        scored_prompt_id="test-id",
    )

    assert score.raw_score_value == score_value


@pytest.mark.parametrize("score_value", ["0", "0.99", "10.01", "11", "-3"])
def test_numeric_range_response_handler_rejects_values_outside_range(score_value: str) -> None:
    handler = NumericRangeResponseHandler(
        response_handler=JsonSchemaResponseHandler(numeric_value=True), minimum_value=1, maximum_value=10
    )

    with pytest.raises(InvalidJsonException, match="must be between 1 and 10"):
        handler.parse(
            response_text=f'{{"score_value": "{score_value}", "rationale": "test"}}',
            scorer_identifier=SCORER_IDENTIFIER,
            scored_prompt_id="test-id",
        )


@pytest.mark.parametrize("score_value", ["high", "nan"])
def test_numeric_range_response_handler_rejects_non_numeric_from_wrapped_handler(score_value: str) -> None:
    # A caller-supplied wire-format handler may not validate numbers itself.
    handler = NumericRangeResponseHandler(
        response_handler=CallableResponseHandler(parser=lambda _: {"score_value": score_value, "rationale": "r"}),
        minimum_value=1,
        maximum_value=10,
    )

    with pytest.raises(InvalidJsonException):
        handler.parse(response_text="ignored", scorer_identifier=SCORER_IDENTIFIER, scored_prompt_id="test-id")


def test_numeric_range_response_handler_replay_identifier_includes_range() -> None:
    wrapped = JsonSchemaResponseHandler(numeric_value=True)
    handler = NumericRangeResponseHandler(response_handler=wrapped, minimum_value=1, maximum_value=10)

    identifier = handler._get_replay_identifier()

    assert identifier is not None
    assert identifier["wrapped"] == wrapped._get_replay_identifier()
    assert identifier["minimum_value"] == 1
    assert identifier["maximum_value"] == 10
    assert handler.json_response_config == wrapped.json_response_config


def _top_level_parser(response: str, *, key: str = "score_value") -> dict[str, Any]:
    return {key: response, "rationale": "r"}


def test_json_schema_response_handler_identifier_includes_all_behavioral_fields() -> None:
    default = JsonSchemaResponseHandler().get_identifier()
    custom = JsonSchemaResponseHandler(
        score_value_output_key="verdict",
        rationale_output_key="explanation",
        description_output_key="desc",
        metadata_output_key="meta",
        category_output_key="cat",
        response_schema={"type": "object"},
        numeric_value=True,
        category_conflict_policy=CategoryConflictPolicy.PREFER_RESPONSE,
    ).get_identifier()

    assert default.hash != custom.hash
    assert custom.params["score_value_output_key"] == "verdict"
    assert custom.params["rationale_output_key"] == "explanation"
    assert custom.params["description_output_key"] == "desc"
    assert custom.params["metadata_output_key"] == "meta"
    assert custom.params["category_output_key"] == "cat"
    assert custom.params["response_schema"] == {"type": "object"}
    assert custom.params["numeric_value"] is True
    assert custom.params["category_conflict_policy"] == "prefer_response"


def test_callable_response_handler_identifier_includes_output_keys_even_without_replay() -> None:
    def local_parser(_: str) -> dict[str, Any]:
        return {"score_value": "true", "verdict": "false", "rationale": "r"}

    first = CallableResponseHandler(parser=local_parser)
    second = CallableResponseHandler(parser=local_parser, score_value_output_key="verdict")

    # Replay opts out for local callables without parser_fingerprint, yet behavioral identity differs.
    assert first._get_replay_identifier() is None
    assert second._get_replay_identifier() is None
    assert first.get_identifier().hash != second.get_identifier().hash
    assert second.get_identifier().params["score_value_output_key"] == "verdict"

    partial_a = CallableResponseHandler(
        parser=partial(_top_level_parser, key="a"),
        parser_fingerprint="v1",
    )
    partial_b = CallableResponseHandler(
        parser=partial(_top_level_parser, key="b"),
        parser_fingerprint="v1",
    )
    assert partial_a.get_identifier().hash != partial_b.get_identifier().hash


def test_wrapper_response_handlers_include_child_handler_identifier() -> None:
    inner = CallableResponseHandler(parser=_top_level_parser, score_value_output_key="verdict")
    tf_handler = TrueFalseResponseHandler(response_handler=inner)
    range_handler = NumericRangeResponseHandler(response_handler=inner, minimum_value=1, maximum_value=5)

    assert tf_handler.get_identifier().children["response_handler"] == inner.get_identifier()
    range_ident = range_handler.get_identifier()
    assert range_ident.children["response_handler"] == inner.get_identifier()
    assert range_ident.params["minimum_value"] == 1
    assert range_ident.params["maximum_value"] == 5
