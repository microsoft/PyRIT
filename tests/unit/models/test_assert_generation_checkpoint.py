# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any

import pytest
from pydantic import ValidationError
from unit.assert_mocks import (
    make_assert_checkpoint,
    make_assert_config,
    make_assert_group_content,
    make_assert_systematization,
    make_assert_taxonomy,
)

from pyrit.models import (
    AssertBehaviorPreset,
    AssertDimension,
    AssertGeneratedGroupContent,
    AssertGenerationCheckpoint,
    AssertGenerationProvenance,
    AssertGenerationStage,
    AssertSuitePreset,
    AssertTaxonomy,
)

_BEHAVIOR_PRESET: dict[str, Any] = {
    "name": "prompt_injection",
    "version": "1.0",
    "tags": ["security"],
    "applicable_to": ["agent"],
    "summary": "Detect instructions followed from untrusted content.",
    "description": "The assistant follows instructions embedded in retrieved content.",
}

_SUITE_PRESET: dict[str, Any] = {
    "name": "travel_planner",
    "version": "1.0",
    "summary": "A travel planning assistant.",
    "context": "The target plans trips with booking tools.",
    "behaviors": ["prompt_injection", "actionability_failures"],
}


def _generated_dimensions(*, definition: str = "Generated condition") -> tuple[AssertDimension, ...]:
    return (
        AssertDimension.model_validate(
            {
                "name": "channel",
                "description": "Where the customer asks.",
                "levels": [{"name": f"level_{index}", "definition": f"{definition} {index}."} for index in range(3)],
            }
        ),
    )


def _described_policy_checkpoint() -> AssertGenerationCheckpoint:
    return AssertGenerationCheckpoint(
        config=make_assert_config(dimensions=[{"name": "channel", "description": "Where the customer asks."}]),
        systematization=make_assert_systematization(),
        taxonomy=make_assert_taxonomy(),
        dimensions=_generated_dimensions(),
        provenance=AssertGenerationProvenance(
            systematization_conversation_id="research",
            taxonomy_conversation_id="taxonomy",
            dimensions_conversation_id="dimensions",
        ),
    )


def _edited_taxonomy(*, behavior_name: str = "customer_support") -> AssertTaxonomy:
    data = make_assert_taxonomy().model_dump(mode="json")
    data["behavior"]["name"] = behavior_name
    data["behavior_categories"][0]["definition"] = "Explains the published return and exchange policy."
    data["behavior_categories"][1]["permissible"] = True
    return AssertTaxonomy.model_validate(data)


def _progress_fields(*, completed: int) -> dict[str, Any]:
    stages = {
        "systematization": make_assert_systematization(),
        "taxonomy": make_assert_taxonomy(),
        "dimensions": make_assert_config().dimensions,
    }
    return dict(list(stages.items())[:completed])


@pytest.mark.parametrize(
    "included, extra, message",
    [
        (["taxonomy"], {}, "taxonomy needs the systematization"),
        (["systematization", "dimensions"], {}, "Dimensions need the taxonomy"),
        ([], {"groups": (make_assert_group_content(),)}, "Seed groups need a taxonomy and dimensions"),
        ([], {"completed_batches": 1}, "Seed groups need a taxonomy and dimensions"),
        (
            ["systematization", "taxonomy"],
            {"groups": (make_assert_group_content(),)},
            "Seed groups need a taxonomy and dimensions",
        ),
    ],
)
def test_checkpoint_rejects_out_of_order_progress(included: list[str], extra: dict[str, Any], message: str) -> None:
    available = _progress_fields(completed=3)
    fields = {name: available[name] for name in included}
    with pytest.raises(ValidationError, match=message):
        AssertGenerationCheckpoint(config=make_assert_config(), **fields, **extra)


def test_checkpoint_rejects_systematization_for_another_behavior() -> None:
    with pytest.raises(ValidationError, match="systematization behavior must match"):
        AssertGenerationCheckpoint(
            config=make_assert_config(behavior_name="other_behavior"), systematization=make_assert_systematization()
        )


def test_checkpoint_rejects_taxonomy_for_another_behavior() -> None:
    with pytest.raises(ValidationError, match="taxonomy behavior must match"):
        AssertGenerationCheckpoint(
            config=make_assert_config(),
            systematization=make_assert_systematization(),
            taxonomy=_edited_taxonomy(behavior_name="other_behavior"),
        )


@pytest.mark.parametrize(
    "dimensions, message",
    [
        ((), "match the requested dimensions in order"),
        ((AssertDimension(name="channel", description="Where the customer asks."),), "needs explicit levels"),
        (_generated_dimensions(), "match explicitly configured dimension levels"),
    ],
)
def test_checkpoint_rejects_dimensions_inconsistent_with_explicit_config(
    dimensions: tuple[AssertDimension, ...], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        AssertGenerationCheckpoint.model_validate({**make_assert_checkpoint().model_dump(), "dimensions": dimensions})


def test_checkpoint_rejects_generated_levels_that_differ_from_level_count() -> None:
    checkpoint = _described_policy_checkpoint()
    with pytest.raises(ValidationError, match="dimension_level_count"):
        AssertGenerationCheckpoint.model_validate(
            {**checkpoint.model_dump(), "config": {**checkpoint.config.model_dump(), "dimension_level_count": 4}}
        )


def test_checkpoint_rejects_more_groups_than_requested() -> None:
    groups = (make_assert_group_content(index=0), make_assert_group_content(index=1))
    with pytest.raises(ValidationError, match="Requested 1 seed groups, the checkpoint has 2"):
        make_assert_checkpoint(config=make_assert_config(group_count=1), groups=groups)


def test_checkpoint_rejects_duplicate_group_ids() -> None:
    group = make_assert_group_content(index=0)
    with pytest.raises(ValidationError, match="group IDs must be unique"):
        make_assert_checkpoint(groups=(group, group))


@pytest.mark.parametrize(
    "assignment",
    [
        {"behavior": "unknown_category", "channel": "chat"},
        {"behavior": "public_policy", "channel": "email"},
        {"behavior": "public_policy"},
        {"behavior": "public_policy", "channel": "chat", "tone": "polite"},
    ],
)
def test_checkpoint_rejects_invalid_group_assignment(assignment: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="Invalid coverage assignment for group 'group_000000'"):
        make_assert_checkpoint(groups=(make_assert_group_content(assignment=assignment),))


@pytest.mark.parametrize(
    "completed, group_count, expected",
    [
        (0, 0, AssertGenerationStage.SYSTEMATIZATION),
        (1, 0, AssertGenerationStage.TAXONOMY),
        (2, 0, AssertGenerationStage.DIMENSIONS),
        (3, 0, AssertGenerationStage.SEED_GROUPS),
        (3, 1, AssertGenerationStage.SEED_GROUPS),
        (3, 2, None),
    ],
)
def test_next_stage_follows_completed_progress(
    completed: int, group_count: int, expected: AssertGenerationStage | None
) -> None:
    groups = tuple(make_assert_group_content(index=index) for index in range(group_count))
    checkpoint = AssertGenerationCheckpoint(
        config=make_assert_config(group_count=2), groups=groups, **_progress_fields(completed=completed)
    )
    assert checkpoint.next_stage == expected


def test_conversation_ids_keep_creation_order_without_duplicates() -> None:
    groups = (
        make_assert_group_content(index=0, conversation_id="batch_a"),
        make_assert_group_content(index=1, conversation_id="batch_a"),
        make_assert_group_content(index=2, conversation_id="batch_b"),
        make_assert_group_content(index=3, conversation_id="taxonomy"),
    )
    checkpoint = make_assert_checkpoint(groups=groups, completed_batches=3)
    assert checkpoint.conversation_ids == ("research", "taxonomy", "batch_a", "batch_b")


def test_conversation_ids_of_a_new_request_are_empty() -> None:
    assert AssertGenerationCheckpoint(config=make_assert_config()).conversation_ids == ()


def test_checkpoint_round_trips_through_json() -> None:
    checkpoint = make_assert_checkpoint(
        groups=(make_assert_group_content(index=0), make_assert_group_content(index=1)), completed_batches=1
    )
    restored = AssertGenerationCheckpoint.model_validate_json(checkpoint.model_dump_json())
    assert restored == checkpoint
    assert restored.next_stage == AssertGenerationStage.SEED_GROUPS


def test_with_reviewed_policy_taxonomy_edit_keeps_config() -> None:
    checkpoint = make_assert_checkpoint()
    reviewed = checkpoint.with_reviewed_policy(taxonomy=_edited_taxonomy(), dimensions=checkpoint.config.dimensions)
    assert reviewed.config == checkpoint.config
    assert reviewed.taxonomy == _edited_taxonomy()
    assert reviewed.dimensions == checkpoint.dimensions
    assert reviewed.provenance.edited_stages == (AssertGenerationStage.TAXONOMY,)
    assert reviewed.provenance.conversation_ids == checkpoint.provenance.conversation_ids
    assert reviewed.next_stage == AssertGenerationStage.SEED_GROUPS
    assert checkpoint.provenance.edited_stages == ()


def test_with_reviewed_policy_dimension_edit_makes_config_levels_explicit() -> None:
    checkpoint = _described_policy_checkpoint()
    dimensions = _generated_dimensions(definition="Reviewed condition")
    reviewed = checkpoint.with_reviewed_policy(taxonomy=make_assert_taxonomy(), dimensions=list(dimensions))
    assert not checkpoint.config.dimensions[0].levels
    assert reviewed.dimensions == dimensions
    assert reviewed.config.dimensions == dimensions
    assert reviewed.config.model_dump(exclude={"dimensions"}) == checkpoint.config.model_dump(exclude={"dimensions"})
    assert reviewed.provenance.edited_stages == (AssertGenerationStage.DIMENSIONS,)
    assert reviewed.provenance.dimensions_conversation_id == "dimensions"


def test_with_reviewed_policy_dimension_edit_can_change_level_count() -> None:
    checkpoint = _described_policy_checkpoint()
    dimension = AssertDimension.model_validate(
        {
            "name": "channel",
            "levels": [
                {"name": "chat", "definition": "Private chat."},
                {"name": "forum", "definition": "Public forum."},
            ],
        }
    )
    reviewed = checkpoint.with_reviewed_policy(taxonomy=make_assert_taxonomy(), dimensions=[dimension])
    assert reviewed.config.dimensions == (dimension,)
    assert reviewed.dimensions == (dimension,)


def test_with_reviewed_policy_unchanged_review_marks_nothing() -> None:
    checkpoint = _described_policy_checkpoint()
    reviewed = checkpoint.with_reviewed_policy(taxonomy=make_assert_taxonomy(), dimensions=_generated_dimensions())
    assert reviewed == checkpoint
    assert reviewed.provenance.edited_stages == ()


def test_with_reviewed_policy_records_edited_stages_in_stage_order() -> None:
    checkpoint = _described_policy_checkpoint()
    dimensions = _generated_dimensions(definition="Reviewed condition")
    first = checkpoint.with_reviewed_policy(taxonomy=make_assert_taxonomy(), dimensions=dimensions)
    second = first.with_reviewed_policy(taxonomy=_edited_taxonomy(), dimensions=dimensions)
    third = second.with_reviewed_policy(taxonomy=_edited_taxonomy(), dimensions=dimensions)
    assert first.provenance.edited_stages == (AssertGenerationStage.DIMENSIONS,)
    assert second.provenance.edited_stages == (AssertGenerationStage.TAXONOMY, AssertGenerationStage.DIMENSIONS)
    assert third == second


@pytest.mark.parametrize(
    "group_count, completed_batches",
    [(1, 1), (0, 1), (1, 0)],
)
def test_with_reviewed_policy_refuses_after_seed_groups(group_count: int, completed_batches: int) -> None:
    groups = tuple(make_assert_group_content(index=index) for index in range(group_count))
    checkpoint = make_assert_checkpoint(groups=groups, completed_batches=completed_batches)
    with pytest.raises(ValueError, match="can't change after seed groups"):
        checkpoint.with_reviewed_policy(taxonomy=_edited_taxonomy(), dimensions=checkpoint.config.dimensions)


@pytest.mark.parametrize("completed", [0, 1, 2])
def test_with_reviewed_policy_refuses_before_policy_exists(completed: int) -> None:
    checkpoint = AssertGenerationCheckpoint(config=make_assert_config(), **_progress_fields(completed=completed))
    with pytest.raises(ValueError, match="needs a generated taxonomy and dimensions"):
        checkpoint.with_reviewed_policy(taxonomy=make_assert_taxonomy(), dimensions=checkpoint.config.dimensions)


def test_with_reviewed_policy_rejects_renamed_behavior() -> None:
    checkpoint = make_assert_checkpoint()
    with pytest.raises(ValidationError, match="taxonomy behavior must match"):
        checkpoint.with_reviewed_policy(
            taxonomy=_edited_taxonomy(behavior_name="other_behavior"), dimensions=checkpoint.config.dimensions
        )


def test_with_reviewed_policy_rejects_dimensions_without_levels() -> None:
    checkpoint = _described_policy_checkpoint()
    with pytest.raises(ValidationError, match="needs explicit levels"):
        checkpoint.with_reviewed_policy(
            taxonomy=make_assert_taxonomy(),
            dimensions=[AssertDimension(name="channel", description="Where the customer asks.")],
        )


def test_with_reviewed_policy_replaced_dimensions_become_the_request() -> None:
    checkpoint = _described_policy_checkpoint()
    tone = AssertDimension.model_validate(
        {
            "name": "tone",
            "levels": [
                {"name": "calm", "definition": "A calm user."},
                {"name": "upset", "definition": "An upset user."},
            ],
        }
    )
    reviewed = checkpoint.with_reviewed_policy(taxonomy=make_assert_taxonomy(), dimensions=[tone])
    assert reviewed.config.dimensions == (tone,)
    assert reviewed.taxonomy is not None
    assert reviewed.taxonomy.get_coverage_levels(reviewed.dimensions or ())["tone"] == ["calm", "upset"]


def test_provenance_rejects_repeated_edited_stages() -> None:
    with pytest.raises(ValidationError, match="must not repeat"):
        AssertGenerationProvenance(edited_stages=(AssertGenerationStage.TAXONOMY, AssertGenerationStage.TAXONOMY))


def test_provenance_edited_stages_serialize_as_stage_values() -> None:
    assert AssertGenerationProvenance().edited_stages == ()
    provenance = AssertGenerationProvenance(edited_stages=(AssertGenerationStage.DIMENSIONS,))
    assert provenance.model_dump(mode="json")["edited_stages"] == ["dimensions"]
    assert AssertGenerationProvenance.model_validate({"edited_stages": ["taxonomy"]}).edited_stages == (
        AssertGenerationStage.TAXONOMY,
    )


def test_generation_stages_are_in_execution_order() -> None:
    assert [stage.value for stage in AssertGenerationStage] == [
        "systematization",
        "taxonomy",
        "dimensions",
        "seed_groups",
    ]


def test_generated_group_content_forbids_extra_fields_and_blank_text() -> None:
    data = make_assert_group_content().model_dump()
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AssertGeneratedGroupContent.model_validate({**data, "unexpected": True})
    with pytest.raises(ValidationError):
        AssertGeneratedGroupContent.model_validate({**data, "objective": " "})


def test_behavior_preset_accepts_library_fields() -> None:
    preset = AssertBehaviorPreset.model_validate(_BEHAVIOR_PRESET)
    assert preset.tags == ("security",)
    assert preset.suggested_judge_presets == ()


def test_suite_preset_accepts_library_fields() -> None:
    preset = AssertSuitePreset.model_validate(_SUITE_PRESET)
    assert preset.behaviors == ("prompt_injection", "actionability_failures")
    assert preset.applicable_to == ()


@pytest.mark.parametrize(
    "preset_type, data", [(AssertBehaviorPreset, _BEHAVIOR_PRESET), (AssertSuitePreset, _SUITE_PRESET)]
)
def test_presets_forbid_extra_fields(
    preset_type: type[AssertBehaviorPreset] | type[AssertSuitePreset], data: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        preset_type.model_validate({**data, "kind": "behavior"})


@pytest.mark.parametrize("field", ["name", "version", "summary", "description"])
def test_behavior_preset_requires_non_empty_text(field: str) -> None:
    with pytest.raises(ValidationError):
        AssertBehaviorPreset.model_validate({**_BEHAVIOR_PRESET, field: ""})


def test_suite_preset_rejects_repeated_behaviors() -> None:
    with pytest.raises(ValidationError, match="lists a behavior more than once"):
        AssertSuitePreset.model_validate({**_SUITE_PRESET, "behaviors": ["prompt_injection", "prompt_injection"]})


def test_suite_preset_requires_behaviors() -> None:
    with pytest.raises(ValidationError):
        AssertSuitePreset.model_validate({**_SUITE_PRESET, "behaviors": []})
