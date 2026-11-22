# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import inspect
from pathlib import Path
from typing import get_args

import pytest

from pyrit.memory import SeedEntry
from pyrit.memory.dataset_mutation import seed_replacement
from pyrit.models import SeedObjective, SeedPrompt, SeedType
from unit.dispatch_exhaustiveness import assert_dispatch_exhaustiveness


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


@pytest.mark.parametrize("extend_literal", [False, True], ids=["current-literal", "extended-literal"])
def test_seed_replacement_dispatch_type_exhaustiveness(tmp_path: Path, *, extend_literal: bool) -> None:
    seed_types = [*get_args(SeedType), *(["future"] if extend_literal else [])]
    probe_source = "\n".join(
        [
            "import copy",
            "import json",
            "from typing import TYPE_CHECKING, Any, Literal, assert_never",
            "from pyrit.memory.dataset_mutation import (",
            "    _SeedProjection,",
            "    _validate_stored_prompt,",
            "    reject_unsafe_seed_input,",
            ")",
            f"SeedType = Literal[{', '.join(repr(seed_type) for seed_type in seed_types)}]",
            "class SeedRecord:",
            "    seed_type: SeedType",
            "    def __getattr__(self, name: str) -> Any:",
            "        raise AttributeError(name)",
            inspect.getsource(seed_replacement),
        ]
    )
    assert_dispatch_exhaustiveness(
        tmp_path=tmp_path,
        probe_source=probe_source,
        extended=extend_literal,
        assertions=["assert_never(record.seed_type)"],
        new_member='Literal["future"]',
    )
