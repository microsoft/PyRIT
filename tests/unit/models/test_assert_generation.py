# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from pydantic import ValidationError
from unit.assert_mocks import make_assert_config, make_assert_taxonomy

from pyrit.models import AssertGenerationConfig, AssertGroupMetadata, AssertTaxonomy


@pytest.fixture
def group_metadata() -> AssertGroupMetadata:
    config = make_assert_config()
    return AssertGroupMetadata(
        group_id="group_0",
        behavior_category="public_policy",
        dimension_values={"channel": "chat"},
        config=config,
        taxonomy=make_assert_taxonomy(),
        dimensions=config.dimensions,
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"group_count": 0},
        {"group_count": True},
        {"group_count": -1},
        {"group_count": 100001},
        {"include_user_prompt": False, "include_adversarial_prompt": False},
        {"include_user_prompt": "true"},
        {"behavior_description": " "},
        {"web_search": "true"},
        {"random_seed": True},
        {"target_system_prompt": " "},
        {"target_system_prompt": "Fixed", "target_owns_instructions": True},
        {"dimensions": [{"name": "behavior", "description": "Reserved."}]},
        {"dimensions": [{"name": "channel", "levels": [{"name": "only", "definition": "One level."}]}]},
        {"dimensions": [{"name": "channel", "description": "A"}, {"name": "channel", "description": "B"}]},
        {"sampling": {"method": "stratified", "stratify_by": ["unknown"]}},
    ],
)
def test_invalid_generation_request(updates: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_assert_config(**updates)


def test_group_count_is_explicit_and_configuration_round_trips() -> None:
    with pytest.raises(ValidationError, match="group_count"):
        AssertGenerationConfig(behavior_name="test", behavior_description="An English requirement.")
    config = make_assert_config(group_count=1)
    assert config.group_count == 1
    assert not config.web_search
    assert AssertGenerationConfig.model_validate_json(config.model_dump_json()) == config


def test_dimension_names_allow_leading_underscores() -> None:
    dimension = make_assert_config().dimensions[0].model_copy(update={"name": "_channel"})
    config = make_assert_config(dimensions=[dimension])
    assert config.dimensions[0].name == "_channel"


def test_mixed_explicit_and_generated_dimensions_are_rejected() -> None:
    config = make_assert_config()
    with pytest.raises(ValidationError, match="every dimension"):
        make_assert_config(
            dimensions=[config.dimensions[0], {"name": "persona", "description": "Who makes the request."}]
        )


def test_policy_stance_is_a_strict_boolean() -> None:
    raw = make_assert_taxonomy().model_dump(mode="json")
    raw["behavior_categories"][0]["permissible"] = "false"
    with pytest.raises(ValidationError, match="boolean"):
        AssertTaxonomy.model_validate(raw)


def test_metadata_retains_the_configuration_and_assignment(group_metadata: AssertGroupMetadata) -> None:
    assert AssertGroupMetadata.from_metadata(group_metadata.to_metadata()) == group_metadata
    raw = group_metadata.to_metadata()
    raw[AssertGroupMetadata.METADATA_KEY]["config"]["behavior_name"] = "changed"
    with pytest.raises(ValidationError, match="taxonomy behavior"):
        AssertGroupMetadata.from_metadata(raw)


@pytest.mark.parametrize(
    "field, value",
    [
        ("behavior_category", "unknown"),
        ("dimension_values", {"channel": "unknown"}),
        ("dimension_values", {"behavior": "public_policy", "channel": "chat"}),
        ("dimension_values", {}),
    ],
)
def test_metadata_rejects_unknown_assignments(
    *, group_metadata: AssertGroupMetadata, field: str, value: object
) -> None:
    raw = group_metadata.to_metadata()
    raw[AssertGroupMetadata.METADATA_KEY][field] = value
    with pytest.raises(ValidationError, match="coverage assignment"):
        AssertGroupMetadata.from_metadata(raw)


@pytest.mark.parametrize("metadata", [None, {}])
def test_missing_generation_metadata_is_explicit(metadata: dict[str, object] | None) -> None:
    with pytest.raises(ValueError, match="no metadata"):
        AssertGroupMetadata.from_metadata(metadata)
