# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from pydantic import ValidationError
from unit.assert_mocks import make_assert_config, make_assert_result

from pyrit.executor.promptgen import AssertGeneratorResult
from pyrit.models import AdversarialPrompts, AssertGroupMetadata, SeedPrompt


def test_result_round_trip_preserves_literal_data_and_prompt_associations() -> None:
    original = make_assert_result().model_copy(
        update={
            "generation_targets": {
                "generation": {"class_name": "GenerationTarget"},
                "research": {"class_name": "ResearchTarget"},
            }
        }
    )
    group = original.seed_groups[0]
    assert group.objective.metadata is not None
    group.objective.metadata[AssertGroupMetadata.METADATA_KEY]["group_id"] = " external-id "
    group.prompts[0].value = "\n{{ literal }} {% invalid %}\n"
    restored = AssertGeneratorResult.model_validate_json(original.model_dump_json())
    assert restored.model_dump(mode="json") == original.model_dump(mode="json")
    assert restored.seed_groups[0].adversarial_prompts is not None


def test_result_requires_exact_counts_and_unique_group_ids() -> None:
    result = make_assert_result()
    raw = result.model_dump(mode="json")
    raw["seed_groups"].pop()
    with pytest.raises(ValidationError, match="Requested 6 seed groups, received 5"):
        AssertGeneratorResult.model_validate(raw)
    raw = result.model_dump(mode="json")
    raw["seed_groups"][1]["seeds"][0]["metadata"][AssertGroupMetadata.METADATA_KEY]["group_id"] = "group_0"
    with pytest.raises(ValidationError, match="IDs must be unique"):
        AssertGeneratorResult.model_validate(raw)
    raw = result.model_dump(mode="json")
    raw["seed_groups"].append(raw["seed_groups"][0])
    with pytest.raises(ValidationError, match="Requested 6 seed groups, received 7"):
        AssertGeneratorResult.model_validate(raw)


@pytest.mark.parametrize("field", ["id", "prompt_group_id"])
def test_storage_identifiers_cannot_merge_generated_groups(field: str) -> None:
    raw = make_assert_result().model_dump(mode="json")
    original = raw["seed_groups"][0]["seeds"][0][field]
    if field == "id":
        raw["seed_groups"][1]["seeds"][0][field] = original
    else:
        for seed in raw["seed_groups"][1]["seeds"]:
            seed[field] = original
    with pytest.raises(ValidationError, match="distinct"):
        AssertGeneratorResult.model_validate(raw)


def test_result_validates_the_last_group_assignment() -> None:
    raw = make_assert_result().model_dump(mode="json")
    raw["seed_groups"][-1]["seeds"][0]["metadata"][AssertGroupMetadata.METADATA_KEY]["behavior_category"] = "unknown"
    with pytest.raises(ValidationError, match="coverage assignment"):
        AssertGeneratorResult.model_validate(raw)


@pytest.mark.parametrize("field", ["unique_assignments", "covered_levels", "covered_pairs"])
def test_result_rejects_incorrect_coverage(field: str) -> None:
    raw = make_assert_result().model_dump(mode="json")
    raw["coverage"][field] -= 1
    with pytest.raises(ValidationError, match="actual seed group assignments"):
        AssertGeneratorResult.model_validate(raw)


@pytest.mark.parametrize("context", ["config", "taxonomy", "dimensions"])
def test_result_requires_matching_group_context(context: str) -> None:
    raw = make_assert_result().model_dump(mode="json")
    if context == "config":
        raw["config"]["context"] = "changed"
    elif context == "taxonomy":
        raw["taxonomy"]["behavior"]["name"] = "changed"
    else:
        raw["dimensions"][0]["name"] = "changed"
    with pytest.raises(ValidationError, match="match the generation request"):
        AssertGeneratorResult.model_validate(raw)


@pytest.mark.parametrize("input_name", ["user", "adversarial"])
@pytest.mark.parametrize("requested", [True, False])
def test_result_rejects_mismatched_prompt_inputs(*, input_name: str, requested: bool) -> None:
    raw = make_assert_result(config=make_assert_config(group_count=1)).model_dump(mode="json")
    group = raw["seed_groups"][0]
    if not requested:
        key = "include_user_prompt" if input_name == "user" else "include_adversarial_prompt"
        raw["config"][key] = False
        group["seeds"][0]["metadata"][AssertGroupMetadata.METADATA_KEY]["config"][key] = False
    elif input_name == "user":
        group["seeds"] = [seed for seed in group["seeds"] if seed.get("role") != "user"]
    else:
        group["adversarial_prompts"] = None
        group["seeds"][0]["metadata"].pop(AdversarialPrompts.METADATA_KEY)
    with pytest.raises(ValidationError, match="requested .*prompt"):
        AssertGeneratorResult.model_validate(raw)


@pytest.mark.parametrize("mode", ["fixed", "target_owned"])
def test_result_enforces_target_instruction_ownership(mode: str) -> None:
    config = make_assert_config(
        group_count=1,
        target_system_prompt="Fixed instructions." if mode == "fixed" else None,
        target_owns_instructions=mode == "target_owned",
    )
    raw = make_assert_result(config=config).model_dump(mode="json")
    group = raw["seed_groups"][0]
    if mode == "fixed":
        next(seed for seed in group["seeds"] if seed.get("role") == "system")["value"] = "Different instructions."
    else:
        prompt = SeedPrompt(
            value="Injected instructions.",
            data_type="text",
            role="system",
            sequence=0,
            prompt_group_id=group["seeds"][0]["prompt_group_id"],
        )
        group["seeds"].append(prompt.model_dump(mode="json"))
    with pytest.raises(ValidationError, match="fixed target system prompt|Target-owned instructions"):
        AssertGeneratorResult.model_validate(raw)


def test_provenance_must_refer_to_retained_generation_conversations() -> None:
    raw = make_assert_result().model_dump(mode="json")
    saved = raw["seed_groups"][0]["seeds"][0]["metadata"][AssertGroupMetadata.METADATA_KEY]
    saved["provenance"]["group_generation_conversation_id"] = "unrelated"
    with pytest.raises(ValidationError, match="unknown generation"):
        AssertGeneratorResult.model_validate(raw)
