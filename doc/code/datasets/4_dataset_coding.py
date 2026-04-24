# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.17.3
# ---

# %% [markdown]
# # 4. Contributing Datasets to PyRIT
#
# PyRIT is designed as a flexible framework that doesn't dictate what you should test, but instead makes it easy to test whatever you need. One of the most common contributions to PyRIT is adding new datasets that others can benefit from.
#
# This guide explains how to contribute datasets to PyRIT's source code, whether you're adding:
# - **Jailbreak templates**: Attack patterns that help bypass safety measures
# - **Harm benchmarks**: Test cases for evaluating model safety (`SeedObjectives` or `SeedPrompts`)
# - **System prompts**: Templates for adversarial models, scorers, and converters
#
# There are three primary ways to include datasets in PyRIT:
#
# ## Method 1: YAML Files
#
# YAML files are ideal when the dataset has a compatible license and would be broadly useful to the PyRIT community. Benefits include:
# - Version control integration
# - Easy review and modification
# - Automatic loading via built-in providers
#
# ### Common Locations for YAML Files
#
# **Jailbreak Templates:**
# - Location: `pyrit/datasets/jailbreak/templates/`
# - Usage: Automatically included via `TextJailBreak` classes and used in converters like `TextJailBreakConverter`
#
# **Harm Datasets:**
# - Location: `pyrit/datasets/seed_datasets/local/`
# - Usage: Automatically loaded with `SeedDatasetProvider` for use in various attack scenarios
#
# For details on YAML format, see [Seed Programming](./2_seed_programming.ipynb).
#
# ## Method 2: Remote Dataset Loaders
#
# Remote datasets are preferable when:
# - Licensing requires attribution or limits redistribution
# - The dataset updates frequently and you want the latest version
# - The dataset is large and better hosted externally
#
# Remote datasets are typically fetched from URLs or HuggingFace. To add one, create a `_RemoteDatasetLoader` subclass with helper functions for parsing, caching, and downloading. These loaders are automatically discovered by `SeedDatasetProvider`.
#
# ### Example: DarkBench Remote Loader
#
# Below is a simplified version of the [`DarkBenchDataset`](../../../pyrit/datasets/seed_datasets/remote/darkbench_dataset.py) loader.

# %%
from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt


class SimpleDarkBench(_RemoteDatasetLoader):
    @property
    def dataset_name(self) -> str:
        return "dark_bench"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        # Fetch from HuggingFace
        data = await self._fetch_from_huggingface(
            dataset_name="apart/darkbench",
            config="default ",
            split="train",
            cache=cache,
            data_files="darkbench.tsv",
        )

        # Process into SeedPrompts
        seed_prompts = [
            SeedPrompt(
                value=item["Example"],
                data_type="text",
                dataset_name=self.dataset_name,
                harm_categories=[item["Deceptive Pattern"]],
            )
            for item in data
        ]

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
