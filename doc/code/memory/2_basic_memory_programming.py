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
# # 2. Basic Memory Programming Usage
#
# The `pyrit.memory` module provides functionality to keep track of the conversation history, scoring, data, and more. You can use memory to read and write data. Here is an example that retrieves a normalized conversation:

# %%
from uuid import uuid4

from pyrit.memory import SQLiteMemory
from pyrit.models import MessagePiece

conversation_id = str(uuid4())

message_list = [
    MessagePiece(
        role="user", original_value="Hi, chat bot! This is my initial prompt.", conversation_id=conversation_id
    ),
    MessagePiece(
        role="assistant", original_value="Nice to meet you! This is my response.", conversation_id=conversation_id
    ),
    MessagePiece(
        role="user",
        original_value="Wonderful! This is my second prompt to the chat bot!",
        conversation_id=conversation_id,
    ),
]

memory = SQLiteMemory(db_path=":memory:")

memory.add_message_to_memory(request=message_list[0].to_message())
memory.add_message_to_memory(request=message_list[1].to_message())
memory.add_message_to_memory(request=message_list[2].to_message())

entries = memory.get_conversation(conversation_id=conversation_id)

for entry in entries:
    print(entry)
