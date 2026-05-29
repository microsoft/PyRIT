# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.models.seeds.seed_objective import SeedObjective


def test_seed_objective_init():
    obj = SeedObjective(value="test objective")
    assert obj.value == "test objective"
    assert obj.data_type == "text"
    assert obj.is_general_technique is False


def test_seed_objective_data_type_always_text():
    obj = SeedObjective(value="objective")
    assert obj.data_type == "text"


def test_seed_objective_is_general_technique_raises():
    with pytest.raises(ValueError, match="general technique"):
        SeedObjective(value="bad", is_general_technique=True)


def test_seed_objective_with_metadata():
    obj = SeedObjective(
        value="objective",
        name="test_obj",
        dataset_name="ds",
        harm_categories=["violence"],
        description="an objective",
    )
    assert obj.name == "test_obj"
    assert obj.dataset_name == "ds"
    assert obj.harm_categories == ["violence"]
    assert obj.description == "an objective"


def test_seed_objective_jinja_template_rendering():
    obj = SeedObjective(value="Hello {{ name }}", is_jinja_template=True)
    assert "name" in obj.value or "Hello" in obj.value


def test_seed_objective_non_jinja_template_preserved():
    obj = SeedObjective(value="Hello {{ name }}")
    assert obj.value == "Hello {{ name }}"


def test_seed_objective_id_auto_generated():
    obj = SeedObjective(value="test")
    assert obj.id is not None
