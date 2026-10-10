# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

import pytest
from pydantic import ValidationError

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.score import LlamaGuardCategory, LlamaGuardPolicy

DEFAULT_POLICY_PATH = Path(SCORER_SEED_PROMPT_PATH, "llamaguard", "llamaguard_3_policy.yaml")


def test_default_policy_loads_expected_categories() -> None:
    policy = LlamaGuardPolicy.from_yaml(DEFAULT_POLICY_PATH)

    assert policy.name == "Llama Guard 3 8B MLCommons policy"
    assert policy.version == "3-8B"
    assert policy.category_codes == tuple(f"S{index}" for index in range(1, 15))
    assert "S14: Code Interpreter Abuse." in policy.rendered_categories


def test_policy_renders_optional_descriptions() -> None:
    policy = LlamaGuardPolicy(
        name="custom",
        version="1",
        categories=(LlamaGuardCategory(code="C1", name="Custom", description="Custom definition."),),
    )

    assert policy.rendered_categories == "C1: Custom.\nCustom definition."


def test_policy_rejects_duplicate_codes() -> None:
    with pytest.raises(ValidationError, match="category codes must be unique"):
        LlamaGuardPolicy(
            name="custom",
            version="1",
            categories=(
                LlamaGuardCategory(code="C1", name="First"),
                LlamaGuardCategory(code="C1", name="Second"),
            ),
        )


@pytest.mark.parametrize("code", [" C1", "C1 ", "C,1", "C1\nC2"])
def test_category_rejects_invalid_codes(code: str) -> None:
    with pytest.raises(ValidationError):
        LlamaGuardCategory(code=code, name="Custom")
