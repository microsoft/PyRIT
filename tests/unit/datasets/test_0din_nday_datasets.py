# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pathlib

import pytest

from pyrit.common.path import DATASETS_PATH
from pyrit.models import SeedDataset
from pyrit.models.seeds.seed_prompt import SeedPrompt

ODIN_DIR = pathlib.Path(DATASETS_PATH) / "seed_datasets" / "local" / "0din"

# All 0DIN n-day seed dataset files with expected properties
ODIN_DATASETS = [
    {
        "file": "placeholder_injection.prompt",
        "dataset_name": "0din_placeholder_injection",
        "harm_categories": ["security", "copyright"],
        "num_seeds": 1,
        "seed_type": "prompt",
    },
    {
        "file": "incremental_table_completion.prompt",
        "dataset_name": "0din_incremental_table_completion",
        "harm_categories": ["security", "illegal_substances"],
        "num_seeds": 1,
        "seed_type": "prompt",
    },
    {
        "file": "technical_field_guide.prompt",
        "dataset_name": "0din_technical_field_guide",
        "harm_categories": ["security", "illegal_substances"],
        "num_seeds": 1,
        "seed_type": "prompt",
    },
    {
        "file": "chemical_compiler_debug.prompt",
        "dataset_name": "0din_chemical_compiler_debug",
        "harm_categories": ["security", "illegal_substances"],
        "num_seeds": 1,
        "seed_type": "prompt",
    },
    {
        "file": "correction.prompt",
        "dataset_name": "0din_correction",
        "harm_categories": ["security", "illegal_substances"],
        "num_seeds": 1,
        "seed_type": "prompt",
    },
    {
        "file": "hex_recipe_book.prompt",
        "dataset_name": "0din_hex_recipe_book",
        "harm_categories": ["security", "illegal_substances"],
        "num_seeds": 3,
        "seed_type": "prompt",
    },
]


class TestOdinNdayDatasets:
    """Tests for 0DIN n-day seed datasets contributed by Mozilla 0DIN."""

    def test_odin_directory_exists(self):
        assert ODIN_DIR.exists(), f"0DIN dataset directory not found: {ODIN_DIR}"
        assert ODIN_DIR.is_dir()

    def test_all_expected_files_exist(self):
        for ds in ODIN_DATASETS:
            file_path = ODIN_DIR / ds["file"]
            assert file_path.exists(), f"Missing seed dataset file: {file_path}"

    @pytest.mark.parametrize(
        "dataset_info",
        ODIN_DATASETS,
        ids=[ds["file"] for ds in ODIN_DATASETS],
    )
    def test_dataset_loads_from_yaml(self, dataset_info):
        """Each 0DIN seed dataset file loads successfully via SeedDataset.from_yaml_file()."""
        file_path = ODIN_DIR / dataset_info["file"]
        dataset = SeedDataset.from_yaml_file(file_path)
        assert isinstance(dataset, SeedDataset)

    @pytest.mark.parametrize(
        "dataset_info",
        ODIN_DATASETS,
        ids=[ds["file"] for ds in ODIN_DATASETS],
    )
    def test_dataset_name(self, dataset_info):
        file_path = ODIN_DIR / dataset_info["file"]
        dataset = SeedDataset.from_yaml_file(file_path)
        assert dataset.dataset_name == dataset_info["dataset_name"]

    @pytest.mark.parametrize(
        "dataset_info",
        ODIN_DATASETS,
        ids=[ds["file"] for ds in ODIN_DATASETS],
    )
    def test_dataset_seed_count(self, dataset_info):
        file_path = ODIN_DIR / dataset_info["file"]
        dataset = SeedDataset.from_yaml_file(file_path)
        assert len(dataset.seeds) == dataset_info["num_seeds"]

    @pytest.mark.parametrize(
        "dataset_info",
        ODIN_DATASETS,
        ids=[ds["file"] for ds in ODIN_DATASETS],
    )
    def test_dataset_harm_categories(self, dataset_info):
        file_path = ODIN_DIR / dataset_info["file"]
        dataset = SeedDataset.from_yaml_file(file_path)

        for seed in dataset.seeds:
            for expected_cat in dataset_info["harm_categories"]:
                assert expected_cat in seed.harm_categories, (
                    f"Expected harm category '{expected_cat}' not found in seed harm_categories: {seed.harm_categories}"
                )

    @pytest.mark.parametrize(
        "dataset_info",
        ODIN_DATASETS,
        ids=[ds["file"] for ds in ODIN_DATASETS],
    )
    def test_dataset_seeds_have_text_data_type(self, dataset_info):
        file_path = ODIN_DIR / dataset_info["file"]
        dataset = SeedDataset.from_yaml_file(file_path)

        for seed in dataset.seeds:
            assert seed.data_type == "text"

    @pytest.mark.parametrize(
        "dataset_info",
        ODIN_DATASETS,
        ids=[ds["file"] for ds in ODIN_DATASETS],
    )
    def test_dataset_seeds_have_nonempty_values(self, dataset_info):
        file_path = ODIN_DIR / dataset_info["file"]
        dataset = SeedDataset.from_yaml_file(file_path)

        for seed in dataset.seeds:
            assert seed.value is not None
            assert len(seed.value.strip()) > 0

    @pytest.mark.parametrize(
        "dataset_info",
        ODIN_DATASETS,
        ids=[ds["file"] for ds in ODIN_DATASETS],
    )
    def test_dataset_source_is_0din(self, dataset_info):
        file_path = ODIN_DIR / dataset_info["file"]
        dataset = SeedDataset.from_yaml_file(file_path)

        for seed in dataset.seeds:
            assert seed.source is not None, "Seed source should not be None"
            assert "0din.ai" in seed.source, f"Source should reference 0din.ai, got: {seed.source}"

    @pytest.mark.parametrize(
        "dataset_info",
        ODIN_DATASETS,
        ids=[ds["file"] for ds in ODIN_DATASETS],
    )
    def test_dataset_seeds_have_authors(self, dataset_info):
        file_path = ODIN_DIR / dataset_info["file"]
        dataset = SeedDataset.from_yaml_file(file_path)

        for seed in dataset.seeds:
            assert seed.authors is not None, "Seed authors should not be None"
            assert len(seed.authors) > 0, "Each seed should have at least one author"
            assert "0DIN" in seed.authors, "0DIN should be listed as an author"

    @pytest.mark.parametrize(
        "dataset_info",
        ODIN_DATASETS,
        ids=[ds["file"] for ds in ODIN_DATASETS],
    )
    def test_dataset_seeds_have_0din_nday_group(self, dataset_info):
        file_path = ODIN_DIR / dataset_info["file"]
        dataset = SeedDataset.from_yaml_file(file_path)

        for seed in dataset.seeds:
            assert seed.groups is not None, "Seed groups should not be None"
            assert "0din_nday" in seed.groups, f"Expected '0din_nday' group, got: {seed.groups}"

    @pytest.mark.parametrize(
        "dataset_info",
        ODIN_DATASETS,
        ids=[ds["file"] for ds in ODIN_DATASETS],
    )
    def test_dataset_description_not_empty(self, dataset_info):
        file_path = ODIN_DIR / dataset_info["file"]
        dataset = SeedDataset.from_yaml_file(file_path)

        assert dataset.description is not None
        assert len(dataset.description.strip()) > 0

    def test_hex_recipe_book_is_multi_turn(self):
        """Hex Recipe Book is a multi-turn attack with 3 sequential prompts."""
        file_path = ODIN_DIR / "hex_recipe_book.prompt"
        dataset = SeedDataset.from_yaml_file(file_path)

        assert len(dataset.seeds) == 3
        # Verify seeds are SeedPrompt with sequential sequence numbers
        for seed in dataset.seeds:
            assert isinstance(seed, SeedPrompt), f"Expected SeedPrompt, got: {type(seed)}"
        sequences = [seed.sequence for seed in dataset.prompts]
        assert sequences == [0, 1, 2], f"Expected sequences [0, 1, 2], got: {sequences}"

    def test_get_values_returns_all_prompts(self):
        """Verify get_values() works for all 0DIN datasets."""
        for ds in ODIN_DATASETS:
            file_path = ODIN_DIR / ds["file"]
            dataset = SeedDataset.from_yaml_file(file_path)
            values = dataset.get_values()
            assert len(values) == ds["num_seeds"]
