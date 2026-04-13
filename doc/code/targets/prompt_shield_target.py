# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.0
# ---

# %% [markdown]
# # Prompt Shield Target - optional

# %% [markdown]
# This is a brief tutorial and documentation on using the Prompt Shield Target

# %% [markdown]
# Below is a very quick summary of how Prompt Shield works. You can visit the following links to learn more:\
# (Docs): https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/jailbreak-detection\
# (Quickstart Guide): https://learn.microsoft.com/en-us/azure/ai-services/content-safety/quickstart-jailbreak
#
# You will need to deploy a content safety endpoint on Azure to use this if you haven't already.
#
# For PyRIT, you can use Prompt Shield as a target, or you can use it as a true/false scorer to see if it detected a jailbreak in your prompt.

# %% [markdown]
# ## How It Works in More Detail

# %% [markdown]
# Prompt Shield is a Content Safety resource that detects attacks (jailbreaks) in the prompts it is given.
#
# The body of the HTTP request you send to it looks like this:
# ```json
# {
#     {"userPrompt"}: "this-is-a-user-prompt-as-a-string",
#     {"documents"}: [
#         "here-is-one-document-as-a-string",
#         "here-is-another"
#     ]
# }
# ```
#
# And it returns the following in its response:
# ```json
# {
#     {"userPromptAnalysis"}: {"attackDetected": "true or false"},
#     {"documentsAnalysis"}: [
#         {"attackDetected": "true or false for the first document"},
#         {"attackDetected": "true or false for the second document"}
#     ]
# }
# ```
#
# This document has an example below.
#
# Some caveats and tips:
# * You can send any string you like to either of those two fields, but they have to be strings. Note that this includes the documents. For example, a pdf attachment which is a 'document' may be encoded in base64. You can send a string of the encoded pdf if you like, but you may have to decode it or parse it to achieve your goal in the operation (whatever it may be).
# * Prompt Shield does have a limit to how many characters you can send in one field (as in, one userPrompt or one document). Be careful when sending ASCII art-encoded prompts, because these will often exceed it.
# * The keys for the response are not 'userPrompt' and 'documents', they're 'userPromptAnalysis' and 'documentsAnalysis' as described above. The responses per entry are wrapped with 'attackDetected'. Keep this in mind because it helps diagnose weird bugs with PromptShieldTarget.

# %% [markdown]
# ## Prompt Shield Target Demo

# %% [markdown]
# The environment variable you'll need is `AZURE_CONTENT_SAFETY_API_ENDPOINT`. Make sure to add it to your .env file if you get an error, and that your deployment is in a region where Prompt Shield is supported. Authentication uses Entra ID via `az login`.
#
# PromptShieldTarget is a target that uses Prompt Shield as its backend. Here's an example of how it processes input:

# %%
import os

from pyrit.auth import get_azure_token_provider
from pyrit.executor.attack import ConsoleAttackResultPrinter, PromptSendingAttack
from pyrit.prompt_target import PromptShieldTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

pst = PromptShieldTarget(
    os.environ.get("AZURE_CONTENT_SAFETY_API_ENDPOINT"),
    get_azure_token_provider("https://cognitiveservices.azure.com/.default"),
)
# To use an API key instead of Entra ID auth:
# pst = PromptShieldTarget(os.environ.get("AZURE_CONTENT_SAFETY_API_ENDPOINT"), api_key="your-api-key")

sample_prompt: str = """
Hello! Can you please retrieve the total number of sales in the document?
"""

attack = PromptSendingAttack(objective_target=pst)
result = await attack.execute_async(objective=sample_prompt)  # type: ignore
await ConsoleAttackResultPrinter().print_conversation_async(result=result)  # type: ignore
