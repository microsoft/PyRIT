# ---
# jupyter:
#   jupytext:
#     cell_metadata_filter: -all
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.17.3
#   kernelspec:
#     display_name: pyrit-dev
#     language: python
#     name: python3
# ---

# %% [markdown]
# # 8. Seed Database Management
#
# Beyond storing attack results and conversation history, PyRIT memory also serves as a powerful repository for managing seed datasets. Storing seeds in the database enables:
#
# - **Curation**: Organize prompts with custom metadata like harm categories and sources
# - **Querying**: Filter seeds by type, modality, harm category, or custom attributes
# - **Sharing**: Collaborate across teams (when using Azure SQL Memory)
# - **Persistence**: Access datasets across sessions and projects
#
# As with all memory operations, you can use local `DuckDBMemory` for individual work or `AzureSQLMemory` for team collaboration and cloud persistence.

# %% [markdown]
# ## Adding Seeds to the Database
#
# PyRIT uses content hashing to prevent duplicate seed prompts from being added to memory. The deduplication logic follows these rules:
#
# 1. **Same dataset, duplicate content**: Seed is rejected (not added)
# 2. **Same dataset, modified content**: Seed is accepted (different hash indicates changes)
# 3. **Different dataset, duplicate content**: Seed is accepted (allows the same content across datasets)
#
# This ensures data integrity while allowing intentional duplication across different datasets.

# %%
from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

# Seed Prompts can be created directly, loaded from yaml files, or fetched from built-in datasets
datasets = await SeedDatasetProvider.fetch_datasets_async(dataset_names=["pyrit_example_dataset"])  # type: ignore

print(datasets[0].seeds[0].value)

memory = CentralMemory.get_memory_instance()
await memory.add_seed_datasets_to_memory_async(datasets=datasets, added_by="test")  # type: ignore

# Retrieve the dataset from memory
seeds = memory.get_seeds(dataset_name="pyrit_example_dataset")
print(f"Number of prompts in dataset: {len(seeds)}")

# Note we can add it again without creating duplicates
await memory.add_seed_datasets_to_memory_async(datasets=datasets, added_by="test")  # type: ignore
seeds = memory.get_seeds(dataset_name="pyrit_example_dataset")
print(f"Number of prompts in dataset after re-adding: {len(seeds)}")

# %% [markdown]
# For more information on creating seeds and datasets, including YAML format and programmatic construction, see the [datasets documentation](../datasets/0_dataset.md).

# %% [markdown]
# ## Retrieving Seeds from the Database
#
# Once seeds are stored in memory, you can query them using various criteria. Let's start by exploring what datasets are available.
#
# The example below shows the dataset we just uploaded (`pyrit_example_dataset`), but `get_seed_dataset_names()` returns all datasets in memory.

# %%
all_dataset_names = memory.get_seed_dataset_names()
print("All dataset names in memory:", all_dataset_names)

# %% [markdown]
# ## Querying Seeds by Criteria
#
# Memory provides flexible querying capabilities to filter seeds based on:
# - **Dataset name**: Get all seeds from a specific dataset
# - **Seed type**: Filter for objectives vs. prompts
# - **Data type**: Filter by modality (text, image, audio, video)
# - **Metadata**: Query by format, sample rate, or custom attributes
# - **Harm categories**: Find seeds related to specific harm types
#
# Below are examples demonstrating different query patterns:


# %%
def print_group(seed_group):
    for seed in seed_group.seeds:
        print(seed)
    print("\n")


# Get all seeds in the dataset we just uploaded
seed_groups = memory.get_seed_groups(dataset_name="pyrit_example_dataset")
print("First seed from pyrit_example_dataset:")
print("----------")
print_group(seed_groups[0])

# Filter by SeedObjectives
seed_groups = memory.get_seed_groups(dataset_name="pyrit_example_dataset", seed_type="objective", group_length=[1])
print("First SeedObjective from pyrit_example_dataset without a seedprompt:")
print("----------")
print_group(seed_groups[0])

# Filter by metadata to get seed prompts in .wav format and samplerate 24000 kBits/s
print("First WAV seed in the database")
seed_groups = memory.get_seed_groups(metadata={"format": "wav", "samplerate": 24000})
print("----------")
print_group(seed_groups[0])

# Filter by image seeds
print("First image seed in the dataset")
seed_groups = memory.get_seed_groups(data_types=["image_path"], dataset_name="pyrit_example_dataset")
print("----------")
print_group(seed_groups[0])
