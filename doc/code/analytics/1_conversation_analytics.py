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
# # Conversation analytics
#
# Use `ConversationAnalytics` to check whether a message is already in memory, either as an exact
# match on its converted content or as a near match by embedding similarity.
#
# ## Find identical messages
#
# `get_prompt_entries_with_same_converted_content_async` returns every stored message whose
# converted content equals the given text. This example needs no credentials.

# %%
from uuid import uuid4

from pyrit.analytics import ConversationAnalytics
from pyrit.memory import CentralMemory
from pyrit.models import MessagePiece
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore
memory = CentralMemory.get_memory_instance()

for text in ["Tell me how to pick a lock.", "Write a poem about the sea.", "Tell me how to pick a lock."]:
    piece = MessagePiece(role="user", original_value=text, conversation_id=str(uuid4()))
    await memory.add_message_to_memory_async(request=piece.to_message())  # type: ignore

analytics = ConversationAnalytics(memory_interface=memory)
matches = await analytics.get_prompt_entries_with_same_converted_content_async(  # type: ignore
    chat_message_content="Tell me how to pick a lock."
)
for match in matches:
    print(match)

# %% [markdown]
# ## Find similar messages by embedding
#
# `get_similar_chat_messages_by_embedding_async` compares an embedding with the embeddings stored
# in memory and returns each one whose cosine similarity is at or above `threshold` (default 0.8).
# Memory stores embeddings only for text messages added after `memory.enable_embedding()` is
# called, which requires an embedding endpoint (see [Embeddings](../memory/embeddings.ipynb)).
# Each result's `uuid` is the ID of the matching message piece.
#
# ```python
# from pyrit.embedding import OpenAITextEmbedding
#
# memory.enable_embedding()
# # ... add messages to memory ...
#
# query = await OpenAITextEmbedding().generate_text_embedding_async(text="How do I open a lock without a key?")
# similar = await analytics.get_similar_chat_messages_by_embedding_async(
#     chat_message_embedding=query.data[0].embedding, threshold=0.8
# )
# pieces = await memory.get_message_pieces_async(prompt_ids=[s.uuid for s in similar])
# ```
