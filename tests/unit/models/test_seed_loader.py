# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.models.seeds import (
    SeedDataset,
    SeedObjective,
    SeedPrompt,
    load_seed_dataset_from_yaml,
    load_seed_from_yaml,
    load_seed_prompt_from_yaml_with_required_parameters,
)


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_load_seed_from_yaml_returns_typed_seed(tmp_path):
    yaml_file = _write(tmp_path, "p.yaml", "value: hello world\ndata_type: text\n")

    loaded = load_seed_from_yaml(yaml_file, cls=SeedPrompt)

    assert isinstance(loaded, SeedPrompt)
    assert loaded.value == "hello world"
    # Loader is the trust boundary — it sets the jinja-template marker exactly once.
    assert loaded.is_jinja_template is True


def test_load_seed_from_yaml_supports_objective(tmp_path):
    yaml_file = _write(tmp_path, "o.yaml", "value: stop the attacker\n")

    loaded = load_seed_from_yaml(yaml_file, cls=SeedObjective)

    assert isinstance(loaded, SeedObjective)
    assert loaded.value == "stop the attacker"
    assert loaded.is_jinja_template is True


def test_load_seed_from_yaml_overrides_in_file_value(tmp_path):
    # An ``is_jinja_template: false`` in the YAML must not let the file claim it is untrusted —
    # the loader's trust claim wins.
    yaml_file = _write(tmp_path, "p.yaml", "value: x\nis_jinja_template: false\n")

    loaded = load_seed_from_yaml(yaml_file, cls=SeedPrompt)

    assert loaded.is_jinja_template is True


def test_load_seed_from_yaml_accepts_string_path(tmp_path):
    yaml_file = _write(tmp_path, "p.yaml", "value: x\n")

    loaded = load_seed_from_yaml(str(yaml_file), cls=SeedPrompt)

    assert loaded.value == "x"


def test_load_seed_from_yaml_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_seed_from_yaml(tmp_path / "missing.yaml", cls=SeedPrompt)


def test_load_seed_from_yaml_empty_file_raises(tmp_path):
    yaml_file = _write(tmp_path, "empty.yaml", "")

    with pytest.raises(ValueError, match="is empty"):
        load_seed_from_yaml(yaml_file, cls=SeedPrompt)


def test_load_seed_from_yaml_top_level_list_raises(tmp_path):
    yaml_file = _write(tmp_path, "list.yaml", "- a\n- b\n")

    with pytest.raises(ValueError, match="must contain a mapping"):
        load_seed_from_yaml(yaml_file, cls=SeedPrompt)


def test_load_seed_from_yaml_invalid_yaml_raises(tmp_path):
    yaml_file = _write(tmp_path, "bad.yaml", "value: [unterminated\n")

    with pytest.raises(ValueError, match="Invalid YAML file"):
        load_seed_from_yaml(yaml_file, cls=SeedPrompt)


def test_load_seed_dataset_from_yaml_marks_seeds_as_trusted(tmp_path):
    yaml_file = _write(
        tmp_path,
        "ds.yaml",
        "name: tiny\nseeds:\n  - value: a\n  - value: b\n",
    )

    dataset = load_seed_dataset_from_yaml(yaml_file)

    assert isinstance(dataset, SeedDataset)
    assert [s.value for s in dataset.seeds] == ["a", "b"]
    # Trust marker propagates from the loader to each nested seed.
    assert all(s.is_jinja_template for s in dataset.seeds)


def test_load_seed_dataset_from_yaml_empty_raises(tmp_path):
    yaml_file = _write(tmp_path, "empty.yaml", "")

    with pytest.raises(ValueError, match="is empty"):
        load_seed_dataset_from_yaml(yaml_file)


def test_load_seed_prompt_from_yaml_with_required_parameters_succeeds(tmp_path):
    yaml_file = _write(
        tmp_path,
        "t.yaml",
        "value: hello {{ name }}\nparameters:\n  - name\n",
    )

    loaded = load_seed_prompt_from_yaml_with_required_parameters(yaml_file, ["name"])

    assert isinstance(loaded, SeedPrompt)
    assert loaded.parameters == ["name"]


def test_load_seed_prompt_from_yaml_with_required_parameters_missing_raises(tmp_path):
    yaml_file = _write(
        tmp_path,
        "t.yaml",
        "value: hello {{ name }}\nparameters:\n  - name\n",
    )

    with pytest.raises(ValueError, match="Template must have these parameters: name, age"):
        load_seed_prompt_from_yaml_with_required_parameters(yaml_file, ["name", "age"])


def test_load_seed_prompt_from_yaml_with_required_parameters_custom_error(tmp_path):
    yaml_file = _write(tmp_path, "t.yaml", "value: no params here\n")

    with pytest.raises(ValueError, match="bespoke"):
        load_seed_prompt_from_yaml_with_required_parameters(yaml_file, ["foo"], error_message="bespoke")


def test_classmethod_shims_delegate_to_loader(tmp_path):
    # Verifies the public classmethod surface (Seed.from_yaml_file et al.)
    # produces the same result as the loader functions — i.e. the shims are honest.
    yaml_file = _write(tmp_path, "p.yaml", "value: x\n")

    via_classmethod = SeedPrompt.from_yaml_file(yaml_file)
    via_function = load_seed_from_yaml(yaml_file, cls=SeedPrompt)

    assert via_classmethod.value == via_function.value
    assert via_classmethod.is_jinja_template == via_function.is_jinja_template is True
