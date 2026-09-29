# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import ast
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import jupytext
import pytest

from pyrit.converter import AsciiSmugglerConverter
from pyrit.datasets import SeedDatasetProvider
from pyrit.models import SeedDataset, SeedGroup, SeedObjective, SeedPrompt
from pyrit.scenario import DatasetAttackConfiguration

_NOTEBOOK_PATH = Path(__file__).parents[3] / "doc" / "code" / "scenarios" / "1_common_scenario_parameters.ipynb"


@pytest.fixture(params=[".py", ".ipynb"])
def dataset_configuration_cell(request: pytest.FixtureRequest) -> str:
    notebook = jupytext.read(_NOTEBOOK_PATH.with_suffix(request.param))
    cells = [
        cell.source
        for cell in notebook.cells
        if cell.cell_type == "code" and "datasets = await SeedDatasetProvider.fetch_datasets_async" in cell.source
    ]
    assert len(cells) == 1
    return cells[0]


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "invalid_value",
    [
        "First line\nSecond line",
        "First line\r\nSecond line",
        "Tab\tseparated",
        "Null\x00character",
        "Delete\x7fcharacter",
        "Non-ASCII caf\u00e9",
    ],
)
async def test_dataset_configuration_filters_before_sampling_async(
    *, dataset_configuration_cell: str, invalid_value: str
) -> None:
    printable_ascii = "".join(chr(codepoint) for codepoint in range(0x20, 0x7F))
    valid_groups = [
        SeedGroup(seeds=[SeedObjective(value=printable_ascii)]),
        SeedGroup(seeds=[SeedObjective(value="A second printable objective.")]),
    ]
    invalid_group = SeedGroup(seeds=[SeedObjective(value=invalid_value)])
    mixed_group = SeedGroup(
        seeds=[SeedObjective(value="Printable objective."), SeedPrompt(value=invalid_value, data_type="text")]
    )
    dataset = SeedDataset(
        seeds=[*invalid_group.seeds, *valid_groups[0].seeds, *mixed_group.seeds, *valid_groups[1].seeds]
    )
    original_values = [seed.value for seed in dataset.seeds]
    namespace: dict[str, Any] = {"DatasetAttackConfiguration": DatasetAttackConfiguration}
    code = compile(dataset_configuration_cell, str(_NOTEBOOK_PATH), "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)

    with patch.object(
        SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock, return_value=[dataset]
    ) as fetch_datasets:
        await eval(code, namespace)

    fetch_datasets.assert_awaited_once_with(dataset_names=["harmbench"])
    assert [group.seeds for group in namespace["seed_groups"]] == [group.seeds for group in valid_groups]
    assert [seed.value for seed in dataset.seeds] == original_values

    config = namespace["dataset_config"]
    assert isinstance(config, DatasetAttackConfiguration)
    assert config.max_dataset_size == 2
    selected_groups = await config.get_attack_seed_groups_async()
    assert len(selected_groups) == 2

    converter = AsciiSmugglerConverter()
    selected_values = [seed.value for group in selected_groups for seed in group.seeds]
    assert set(selected_values) == {printable_ascii, "A second printable objective."}
    for value in selected_values:
        _, encoded = converter.encode_message(message=value)
        assert converter.decode_message(message=encoded) == value
