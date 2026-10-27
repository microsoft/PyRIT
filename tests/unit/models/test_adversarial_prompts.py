# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any

import pytest
from pydantic import ValidationError

from pyrit.memory import MemoryInterface
from pyrit.models import (
    AdversarialPrompts,
    AtomicAttackIdentifier,
    AttackSeedGroup,
    AttackTechniqueSeedGroup,
    ComponentIdentifier,
    SeedDataset,
    SeedIdentifier,
    SeedObjective,
    SeedPrompt,
    get_common_json_schema,
    group_seeds_into_attack_groups,
)


def _prompts(*, instructions: str = "Ask about returns.") -> AdversarialPrompts:
    template = SeedPrompt(
        value="Objective: {{ objective }}\n{{ user_role }}",
        data_type="text",
        parameters=["objective", "user_role"],
        response_json_schema=get_common_json_schema("adversarial_chat"),
        is_jinja_template=True,
    )
    return AdversarialPrompts(system_prompt=template.bind_template_parameters(user_role=instructions))


def _group(*, instructions: str = "Ask about returns.") -> AttackSeedGroup:
    return AttackSeedGroup(
        seeds=[
            SeedObjective(value="Explain the return policy."),
            SeedPrompt(value="What is the return period?", data_type="text", role="user"),
        ],
        adversarial_prompts=_prompts(instructions=instructions),
    )


def test_named_adversarial_prompts_are_not_application_messages() -> None:
    group = _group()
    assert len(group.seeds) == 2
    assert [prompt.value for prompt in group.prompts] == ["What is the return period?"]
    assert group.next_message.get_piece().original_value == "What is the return period?"
    assert not group.prepended_conversation
    assert group.adversarial_prompts is not None
    assert group.adversarial_prompts.system_prompt is not None
    assert group.adversarial_prompts.system_prompt not in group.seeds


@pytest.mark.parametrize(
    "instructions",
    [
        "{{ 7 * 7 }}",
        "{% endraw %}{% invalid %}\n{{ objective }}",
        "'quoted' \"text\" \\\n{# a comment #}",
    ],
)
def test_bound_literal_text_survives_validation_and_serialization(instructions: str) -> None:
    group = _group(instructions=instructions)
    restored = AttackSeedGroup.model_validate_json(group.model_dump_json())
    regrouped = group_seeds_into_attack_groups(group.seeds)[0]
    for candidate in (group, restored, regrouped):
        assert candidate.adversarial_prompts is not None
        prompt = candidate.adversarial_prompts.system_prompt
        assert prompt is not None
        assert prompt.parameters == ["objective"]
        assert prompt.response_json_schema == get_common_json_schema("adversarial_chat")
        assert prompt.render_template_value(objective="goal") == f"Objective: goal\n{instructions}"
        assert candidate.logical_id == group.logical_id


def test_named_prompts_survive_technique_composition() -> None:
    group = _group()
    technique = AttackTechniqueSeedGroup(
        seeds=[SeedPrompt(value="Answer as a helpful expert.", data_type="text", is_general_technique=True)]
    )
    merged = group.with_technique(technique=technique)
    assert merged.adversarial_prompts is not None
    assert group.adversarial_prompts is not None
    assert merged.adversarial_prompts.identity == group.adversarial_prompts.identity
    assert merged.adversarial_prompts.system_prompt is not group.adversarial_prompts.system_prompt
    assert all("User instructions" not in prompt.value for prompt in merged.prompts)


def test_prompt_values_parameters_and_schemas_affect_group_identity() -> None:
    original = _group()
    assert original.logical_id == _group().logical_id
    assert original.logical_id != _group(instructions="Ask for private order details.").logical_id
    raw = original.model_dump(mode="json")
    raw["seeds"][0]["metadata"].pop(AdversarialPrompts.METADATA_KEY)
    raw["adversarial_prompts"]["system_prompt"]["parameters"].append("language")
    changed_parameters = AttackSeedGroup.model_validate(raw)
    assert changed_parameters.logical_id != original.logical_id
    raw["adversarial_prompts"]["system_prompt"]["response_json_schema"]["description"] = "A different contract."
    changed_schema = AttackSeedGroup.model_validate(raw)
    assert changed_schema.logical_id != changed_parameters.logical_id


def test_prompt_slot_changes_group_identity() -> None:
    prompt = SeedPrompt(value="A message.", data_type="text")
    first = AttackSeedGroup(
        seeds=[SeedObjective(value="goal")], adversarial_prompts=AdversarialPrompts(first_message=prompt)
    )
    feedback = AttackSeedGroup(
        seeds=[SeedObjective(value="goal")], adversarial_prompts=AdversarialPrompts(feedback_template=prompt)
    )
    assert first.logical_id != feedback.logical_id


def test_atomic_identity_retains_the_named_prompt_contract() -> None:
    group = _group()
    identifier = AtomicAttackIdentifier.build(
        seed_group=group,
        attack_identifier=ComponentIdentifier(class_name="Attack", class_module="tests"),
    )
    assert identifier.logical_seed_group_id == group.logical_id
    assert group.adversarial_prompts is not None
    assert identifier.seed_identifiers[0].params["adversarial_prompts"] == group.adversarial_prompts.identity


def test_conflicting_saved_association_is_rejected() -> None:
    original = _group()
    with pytest.raises(ValidationError, match="conflict"):
        AttackSeedGroup(seeds=original.seeds, adversarial_prompts=_prompts(instructions="A different role."))


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"first_message": {"value": "", "data_type": "text"}},
        {"first_message": {"value": "image.png", "data_type": "image_path"}},
        {"feedback_template": {"value": "Feedback", "response_json_schema": {"type": "object"}}},
        {
            "system_prompt": {"value": "system", "response_json_schema": {"type": "object"}},
            "first_message": {"value": "first", "response_json_schema": {"type": "object"}},
        },
    ],
)
def test_invalid_named_prompts_are_rejected(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AdversarialPrompts.model_validate(values)


@pytest.mark.usefixtures("patch_central_database")
async def test_flat_dataset_and_memory_round_trip_preserve_the_association_async(
    sqlite_instance: MemoryInterface,
) -> None:
    original = _group(instructions="{% endraw %}{{ 7 * 7 }}")
    dataset = SeedDataset(dataset_name="adversarial_groups", seeds=original.seeds)
    for seed in dataset.seeds:
        seed.dataset_name = dataset.dataset_name
    identity = original.logical_id
    copied = SeedDataset.model_validate_json(dataset.model_dump_json())
    assert group_seeds_into_attack_groups(copied.seeds)[0].logical_id == identity
    await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[copied], added_by="operator")
    seeds = await sqlite_instance.get_seeds_async(dataset_name="adversarial_groups")
    restored = group_seeds_into_attack_groups(seeds)[0]
    assert [SeedIdentifier.from_seed(seed).model_dump(mode="json") for seed in restored.seeds] == [
        SeedIdentifier.from_seed(seed).model_dump(mode="json") for seed in copied.seeds
    ]
    assert restored.logical_id == group_seeds_into_attack_groups(copied.seeds)[0].logical_id
    assert restored.adversarial_prompts is not None
    assert original.adversarial_prompts is not None
    assert restored.adversarial_prompts.identity == original.adversarial_prompts.identity
    assert restored.adversarial_prompts.system_prompt is not None
    assert restored.adversarial_prompts.system_prompt.render_template_value(objective="goal") == (
        "Objective: goal\n{% endraw %}{{ 7 * 7 }}"
    )
    assert restored.next_message.get_piece().original_value == "What is the return period?"
