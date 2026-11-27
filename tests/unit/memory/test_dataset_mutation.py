# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.memory import SeedEntry
from pyrit.memory.dataset_mutation import seed_replacement
from pyrit.models import SeedObjective, SeedPrompt


@pytest.mark.usefixtures("patch_central_database")
def test_seed_replacement_unknown_seed_type_keeps_objective_fallback() -> None:
    record = SeedEntry(
        entry=SeedObjective(value="objective text", dataset_name="ds", added_by="tester")
    ).get_seed_record()
    expected = seed_replacement(record)

    replacement = seed_replacement(record.model_copy(update={"seed_type": "future"}))

    assert replacement == {**expected, "seed_type": "future"}
    assert "conditions" in replacement


@pytest.mark.usefixtures("patch_central_database")
def test_seed_replacement_prompt_keeps_prompt_fields() -> None:
    record = SeedEntry(
        entry=SeedPrompt(value="prompt text", data_type="text", dataset_name="ds", added_by="tester")
    ).get_seed_record()

    replacement = seed_replacement(record)

    assert replacement["sequence"] == 0
    assert "conditions" not in replacement
