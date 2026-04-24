# ---
# jupyter:
#   jupytext:
#     cell_metadata_filter: -all
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.17.3
# ---

# %% [markdown]
# # 1. SQLite Memory
#
# The memory SQLite database can be thought of as a normalized source of truth. The memory module is the primary way PyRIT keeps track of requests and responses to targets and scores. Most of this is done automatically. All Prompt Targets write to memory for later retrieval. All scorers also write to memory when scoring.
#
# The schema is found in `memory_models.py` and can be programmatically viewed as follows

# %%
from pyrit.memory import SQLiteMemory

# Use in-memory database to avoid file corruption issues
memory = SQLiteMemory(db_path=":memory:")
memory.print_schema()
