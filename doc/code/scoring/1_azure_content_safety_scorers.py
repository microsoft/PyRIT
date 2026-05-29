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
#
# # 1. Float Scale Scoring using Azure Content Safety API
#
# The Azure Content Safety API is one of our most reliable scorers for detecting harms. Although it isn't very flexible, it's extremely fast and reliable and can be used to score images or text.
#
# In order to use this API, you need to configure a few environment variables:
#
# - AZURE_CONTENT_SAFETY_API_ENDPOINT: The endpoint for the Azure Content Safety API
#
# Authentication uses Entra ID (identity-based) via `az login`. Make sure you have the
# appropriate role (e.g., Cognitive Services User) on the resource.
# from pyrit.auth.azure_auth import get_azure_token_provider
# api_key = get_azure_token_provider("https://cognitiveservices.azure.com/.default")
#
# Note that this api returns a value between 0 and 7. This is different from likert scales, which return a value between 1 and 5. Because both are `float_scale` scores, these values are all normalized to floating point values between 0.0 and 1.0 and can be directly compared. This is sometimes interesting as an operator e.g. if there are scenarios where a `SelfAskLikertScorer` and `AzureContentFilterScorer` produce very different values.
#

# %%
import os

from pyrit.auth import get_azure_token_provider
from pyrit.memory import CentralMemory
from pyrit.models import Message, MessagePiece
from pyrit.score.float_scale.azure_content_filter_scorer import AzureContentFilterScorer
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

# Set up the Azure Content Filter
azure_content_filter = AzureContentFilterScorer(
    api_key=get_azure_token_provider("https://cognitiveservices.azure.com/.default"),
    endpoint=os.environ.get("AZURE_CONTENT_SAFETY_API_ENDPOINT"),
)
# For API key authentication (if local auth is enabled on the resource):
# azure_content_filter = AzureContentFilterScorer(
#     api_key=os.environ.get("AZURE_CONTENT_SAFETY_API_KEY"),
#     endpoint=os.environ.get("AZURE_CONTENT_SAFETY_API_ENDPOINT"),
# )

response = Message(
    message_pieces=[
        MessagePiece(
            role="assistant",
            original_value_data_type="text",
            original_value="I hate you.",
        )
    ]
)
memory = CentralMemory.get_memory_instance()
# need to write it manually to memory as score table has a foreign key constraint
memory.add_message_to_memory(request=response)

# Run the request
scores = await azure_content_filter.score_async(response)  # type: ignore
assert scores[0].get_value() > 0  # azure_severity should be value 2 based on the documentation

for score in scores:
    # score_metadata contains azure_severity original value
    print(f"{score} {score.score_metadata}")
