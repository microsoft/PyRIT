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
#
# Many of these datasets come from published research, including
# BeaverTails [@ji2023beavertails],
# HarmBench [@mazeika2024harmbench],
# JailbreakBench [@chao2024jailbreakbench],
# HarmfulQA [@chu2023harmfulqa],
# CBT-Bench [@zhang2024cbtbench],
# SimpleSafetyTests [@vidgen2023simplesafetytests],
# Do-Not-Answer [@wang2023donotanswer],
# OR-Bench [@cui2024orbench],
# SALAD-Bench [@li2024saladbench],
# SORRY-Bench [@xie2024sorrybench],
# ToxicChat [@lin2023toxicchat],
# SOSBench [@jiang2025sosbench],
# PKU-SafeRLHF [@ji2024pkusaferlhf],
# EquityMedQA [@pfohl2024equitymedqa],
# DarkBench [@darkbench2025],
# Multilingual Alignment Prism [@aakanksha2024multilingual],
# Multilingual Vulnerabilities [@tang2025multilingual],
# VLSU [@palaskar2025vlsu],
# Do Anything Now [@shen2023donotanything],
# AILuminate [@vidgen2024ailuminate],
# and Transphobia Awareness [@scheuerman2025transphobia].

# %%
from pyrit.datasets import SeedDatasetProvider

SeedDatasetProvider.get_all_dataset_names()

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
