# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.1
# ---

# %% [markdown]
# # 1. Loading Built-in Datasets
#
# PyRIT includes many built-in datasets to help you get started with AI red teaming. While PyRIT aims to be unopinionated about what constitutes harmful content, it provides easy mechanisms to use datasets—whether built-in, community-contributed, or your own custom datasets.
#
# **Important Note**: Datasets are best managed through [PyRIT memory](../memory/8_seed_database.ipynb), where data is normalized and can be queried efficiently. However, this guide demonstrates how to load datasets directly as a starting point, and these can easily be imported into the database later.
#
# The following command lists all built-in datasets available in PyRIT. Some datasets are stored locally, while others are fetched remotely from sources like HuggingFace.

# %%
from pyrit.datasets import SeedDatasetProvider

SeedDatasetProvider.get_all_dataset_names()

# %% [markdown]
# ## Dataset Overview
#
# The following table summarizes all built-in datasets with descriptions and source references,
# extracted from the dataset provider docstrings.

# %%
import re


def _extract_dataset_info(provider_class):
    """Extract name, description, and source URL from a dataset provider class."""
    provider = provider_class()
    name = provider.dataset_name
    doc = provider_class.__doc__ or ""

    # Extract first paragraph as description (skip class name line)
    lines = [line.strip() for line in doc.strip().split("\n") if line.strip()]
    desc_lines = []
    for line in lines[1:]:  # skip first line (usually "Loader for...")
        if line.startswith(("Reference", "License", "Warning")):
            break
        if line.startswith(("- ", "[@")):
            break
        desc_lines.append(line)
    description = " ".join(desc_lines).strip()
    if not description and lines:
        description = lines[0]

    # Extract source URL
    url_match = re.search(r"https?://\S+", doc)
    source_url = url_match.group(0).rstrip(",.;)") if url_match else ""

    # Extract citation key if present
    cite_match = re.search(r"\[@(\w+)\]", doc)
    citation = f"[@{cite_match.group(1)}]" if cite_match else ""

    return name, description[:120], source_url, citation


providers = SeedDatasetProvider.get_all_providers()
rows = []
for _cls_name, cls in sorted(providers.items()):
    try:
        name, desc, url, cite = _extract_dataset_info(cls)
        rows.append((name, desc, url, cite))
    except Exception:
        pass

# Print as markdown table
print("| Dataset | Description | Source | Citation |")
print("|---------|-------------|--------|----------|")
for name, desc, url, cite in rows:
    url_cell = url if url else ""
    print(f"| {name} | {desc} | {url_cell} | {cite} |")

# %% [markdown]
# ## Loading Specific Datasets
#
# You can retrieve all built-in datasets using `SeedDatasetProvider.fetch_datasets_async()`, or fetch specific ones by providing dataset names. This returns a list of `SeedDataset` objects containing the seeds.

# %%
datasets = await SeedDatasetProvider.fetch_datasets_async(dataset_names=["airt_illegal", "airt_malware"])  # type: ignore

for dataset in datasets:
    for seed in dataset.seeds:
        print(seed.value)

# %% [markdown]
# ## Adding Datasets to Memory
#
# While loading datasets directly is useful for quick exploration, storing them in PyRIT memory provides significant advantages for managing and querying your test data. Memory allows you to:
# - Query seeds by harm category, data type, or custom metadata
# - Track provenance and versions
# - Share datasets across team members (when using Azure SQL)
# - Avoid duplicate entries
#
# The following example demonstrates adding datasets to memory. For comprehensive details on memory capabilities, see the [memory documentation](../memory/0_memory.md) and [seed database guide](../memory/8_seed_database.ipynb).

# %%
from pyrit.memory import CentralMemory
from pyrit.setup.initialization import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

memory = CentralMemory().get_memory_instance()
await memory.add_seed_datasets_to_memory_async(datasets=datasets, added_by="pyrit")  # type: ignore

# Memory has flexible querying capabilities
memory.get_seeds(harm_categories=["illegal"], seed_type="objective")
