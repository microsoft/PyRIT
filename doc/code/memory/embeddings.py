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
# # Embeddings - optional
#
# PyRIT also allows to get embeddings. The embedding response is a wrapper for the OpenAI embedding API.

# %%
from pprint import pprint

from pyrit.embedding import OpenAITextEmbedding
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

input_text = "hello"
ada_embedding_engine = OpenAITextEmbedding()
embedding_response = await ada_embedding_engine.generate_text_embedding_async(text=input_text)  # type: ignore

pprint(embedding_response, width=280, compact=True)

# %% [markdown]
#
# ## Embeddings Serialization
#
# All the PyRIT's embeddings are easily serializable. This allows you to easily save and load embeddings for later use, and be able to inspect the value of the embeddings offline (since
# embeddings are stored as JSON objects).
#

# %% [markdown]
# To view the json of an embedding

# %%
embedding_response.to_json()

# %% [markdown]
# To save an embedding to disk

# %%
from pyrit.common.path import DB_DATA_PATH

saved_embedding_path = embedding_response.save_to_file(directory_path=DB_DATA_PATH)
print(saved_embedding_path)

# %% [markdown]
# To load an embedding from disk


# %%
from pyrit.common.path import DB_DATA_PATH

saved_embedding_path = embedding_response.save_to_file(directory_path=DB_DATA_PATH)
print(saved_embedding_path)
