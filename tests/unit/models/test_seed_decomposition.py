# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from pyrit.models import DecompositionPath, SeedDataset, SeedObjective, SeedPrompt, build_objective_decomposition_tree

DECOMPOSED_DATASET: dict[str, Any] = {
    "dataset_name": "crm_exfiltration",
    "harm_categories": ["cyber"],
    "seeds": [
        {
            "seed_type": "objective",
            "value": "Exfiltrate customer data from the internal CRM",
            "sub_objectives": [
                {"key": "recon", "value": "Enumerate internal services"},
                {"key": "creds", "value": "Obtain admin credentials"},
                {
                    "key": "phish",
                    "value": "Phish an admin",
                    "prompt_group_alias": "phishing",
                    "sub_objectives": [
                        {"key": "lure", "value": "Write a lure"},
                        {"key": "send", "value": "Send the lure"},
                    ],
                    "paths": [["lure", "send"]],
                },
                {"key": "exfil", "value": "Pull customer records"},
            ],
            "paths": [
                {"name": "Credential theft", "description": "Direct", "steps": ["recon", "creds", "exfil"]},
                ["recon", "phish", "exfil"],
            ],
        },
        {"value": "Hi, IT here. Please reset your password.", "prompt_group_alias": "phishing"},
    ],
}


def _by_value(dataset: SeedDataset) -> dict[str, SeedObjective]:
    return {objective.value: objective for objective in dataset.objectives}


def test_seed_objective_defaults_to_standalone() -> None:
    objective = SeedObjective(value="standalone")

    assert objective.parent_objective_id is None
    assert objective.paths == ()
    assert objective.decomposition_node is not None
    assert not objective.decomposition_node.is_member


def test_seed_objective_rejects_itself_as_parent() -> None:
    objective_id = uuid.uuid4()

    with pytest.raises(ValidationError, match="own parent"):
        SeedObjective(id=objective_id, value="loop", parent_objective_id=objective_id)


def test_seed_objective_rejects_relationships_without_id() -> None:
    with pytest.raises(ValidationError, match="must have an ID"):
        SeedObjective(id=None, value="orphan", parent_objective_id=uuid.uuid4())


def test_seed_objective_without_id_has_no_node() -> None:
    assert SeedObjective(id=None, value="no id").decomposition_node is None


def test_from_dict_flattens_sub_objectives_with_parents_and_paths() -> None:
    dataset = SeedDataset.from_dict(DECOMPOSED_DATASET)
    objectives = _by_value(dataset)
    root = objectives["Exfiltrate customer data from the internal CRM"]
    recon, creds, phish, exfil = (
        objectives[value]
        for value in (
            "Enumerate internal services",
            "Obtain admin credentials",
            "Phish an admin",
            "Pull customer records",
        )
    )

    assert root.parent_objective_id is None
    assert {recon.parent_objective_id, creds.parent_objective_id, phish.parent_objective_id} == {root.id}
    assert objectives["Write a lure"].parent_objective_id == phish.id
    assert [path.steps for path in root.paths] == [(recon.id, creds.id, exfil.id), (recon.id, phish.id, exfil.id)]
    assert (root.paths[0].name, root.paths[0].description) == ("Credential theft", "Direct")
    assert root.paths[1].name is None
    assert phish.paths[0].steps == (objectives["Write a lure"].id, objectives["Send the lure"].id)


def test_from_dict_applies_dataset_defaults_to_sub_objectives() -> None:
    dataset = SeedDataset.from_dict(DECOMPOSED_DATASET)

    for objective in dataset.objectives:
        assert objective.dataset_name == "crm_exfiltration"
        assert objective.harm_categories == ["cyber"]


def test_from_dict_groups_sub_objective_with_prompt_by_alias() -> None:
    dataset = SeedDataset.from_dict(DECOMPOSED_DATASET)
    phish = _by_value(dataset)["Phish an admin"]
    prompt = next(seed for seed in dataset.seeds if isinstance(seed, SeedPrompt))

    assert prompt.prompt_group_id == phish.prompt_group_id
    assert all(seed.prompt_group_id is not None for seed in dataset.seeds)


def test_decomposition_tree_walks_every_level() -> None:
    dataset = SeedDataset.from_dict(DECOMPOSED_DATASET)
    tree = dataset.get_decomposition_tree()
    values = {objective.id: objective.value for objective in dataset.objectives}

    (root_id,) = tree.roots
    assert [(values[node_id], depth) for node_id, depth in tree.walk(root_id)] == [
        ("Exfiltrate customer data from the internal CRM", 0),
        ("Enumerate internal services", 1),
        ("Obtain admin credentials", 1),
        ("Phish an admin", 1),
        ("Write a lure", 2),
        ("Send the lure", 2),
        ("Pull customer records", 1),
    ]


def test_direct_construction_flattens_sub_objectives() -> None:
    dataset = SeedDataset(**DECOMPOSED_DATASET)

    assert len(dataset.objectives) == 7
    assert dataset.get_decomposition_tree().roots == (dataset.objectives[0].id,)


def test_from_yaml_file_flattens_sub_objectives(tmp_path: Path) -> None:
    file = tmp_path / "decomposed.yaml"
    file.write_text(
        "dataset_name: yaml_decomposition\n"
        "seeds:\n"
        "  - seed_type: objective\n"
        "    value: Goal\n"
        "    sub_objectives:\n"
        "      - {key: a, value: Step A}\n"
        "      - {key: b, value: Step B}\n"
        "    paths:\n"
        "      - {name: Only, steps: [a, b]}\n",
        encoding="utf-8",
    )

    dataset = SeedDataset.from_yaml_file(file)
    objectives = _by_value(dataset)

    assert objectives["Goal"].paths[0].steps == (objectives["Step A"].id, objectives["Step B"].id)


def test_objective_seed_type_is_inferred_for_decomposed_seeds() -> None:
    dataset = SeedDataset.from_dict(
        {"seeds": [{"value": "Goal", "sub_objectives": [{"key": "a", "value": "Step"}], "paths": [["a"]]}]}
    )

    assert len(dataset.objectives) == 2


@pytest.mark.parametrize(
    ("seed", "message"),
    [
        (
            {"seed_type": "prompt", "value": "Goal", "sub_objectives": [{"key": "a", "value": "A"}]},
            "Only objectives",
        ),
        ({"value": "Goal", "sub_objectives": [{"value": "A"}]}, "non-empty 'key'"),
        ({"value": "Goal", "sub_objectives": ["A"]}, "must be a mapping"),
        (
            {"value": "Goal", "sub_objectives": [{"key": "a", "value": "A"}, {"key": "a", "value": "B"}]},
            "more than once",
        ),
        (
            {"value": "Goal", "sub_objectives": [{"key": "a", "value": "A", "parent_objective_id": str(uuid.uuid4())}]},
            "takes its parent from its position",
        ),
        ({"value": "Goal", "sub_objectives": [{"key": "a", "value": "A"}], "paths": [["b"]]}, "unknown"),
        ({"value": "Goal", "sub_objectives": [{"key": "a", "value": "A"}], "paths": ["a"]}, "Each path"),
        (
            {"value": "Goal", "sub_objectives": [{"key": "a", "value": "A", "seed_type": "prompt"}]},
            "Only objectives",
        ),
    ],
)
def test_from_dict_rejects_invalid_sub_objectives(seed: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SeedDataset.from_dict({"seeds": [seed]})


def test_decomposition_tree_rejects_missing_parent() -> None:
    dataset = SeedDataset(seeds=[SeedObjective(value="child", parent_objective_id=uuid.uuid4())])

    with pytest.raises(ValueError, match="is missing"):
        dataset.get_decomposition_tree()


def test_build_tree_includes_only_decomposed_objectives() -> None:
    parent = SeedObjective(value="parent")
    child = SeedObjective(value="child", parent_objective_id=parent.id)
    parent.paths = (DecompositionPath(steps=(child.id,)),)
    standalone = SeedObjective(value="standalone")

    tree = build_objective_decomposition_tree([parent, child, standalone])

    assert tree.node_ids == (parent.id, child.id)
