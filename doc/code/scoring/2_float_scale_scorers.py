# ---
# jupyter:
#   jupytext:
#     cell_metadata_filter: -all
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.1
# ---
# %% [markdown]
# # Float-Scale Scorers
# %% [markdown]
# A `float_scale` scorer returns a number normalized to `0.0`–`1.0` (`score.get_value()` is
# a `float`). Use these to quantify *how much* of something is present — severity of harmful
# content, strength of misinformation, riskiness of code — rather than a yes/no.
#
# Different backends use different native ranges (Azure Content Safety is 0–7, a Likert
# scale is 1–5); PyRIT normalizes them all to 0–1 so scores are directly comparable.
#
# This page covers **leaf** float-scale scorers, organized fast → slow. Converting a
# float-scale score to true/false with a threshold is on
# [Combining & stacking scorers](3_combining_scorers.ipynb).
# %%
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

# %% [markdown]
# ## Fast scorers
#
# ### AzureContentFilterScorer
#
# The Azure Content Safety API is fast and reliable for detecting harm categories (hate,
# violence, sexual, self-harm) in text or images. It calls a hosted **classifier** — not a
# generative LLM — so it needs an endpoint and credentials but no chat target.
#
# Set `AZURE_CONTENT_SAFETY_API_ENDPOINT` and authenticate with Entra ID (`az login`).
# %%
import os

from pyrit.auth import get_azure_token_provider
from pyrit.memory import CentralMemory
from pyrit.models import Message, MessagePiece
from pyrit.score import AzureContentFilterScorer

azure_content_filter = AzureContentFilterScorer(
    api_key=get_azure_token_provider("https://cognitiveservices.azure.com/.default"),
    endpoint=os.environ.get("AZURE_CONTENT_SAFETY_API_ENDPOINT"),
)

response = Message(
    message_pieces=[MessagePiece(role="assistant", original_value_data_type="text", original_value="I hate you.")]
)
# The score table has a foreign key on the message, so write it to memory first.
CentralMemory.get_memory_instance().add_message_to_memory(request=response)

scores = await azure_content_filter.score_async(response)  # type: ignore
for score in scores:
    # One score per harm category; score_metadata holds the original 0-7 severity.
    print(f"{score.score_category}: value={score.get_value()} metadata={score.score_metadata}")

# %% [markdown]
# ### PlagiarismScorer
#
# Measures textual overlap between a response and a reference text (longest common
# subsequence, Levenshtein, or Jaccard). Runs locally, no LLM.
# %%
from pyrit.score import PlagiarismScorer
from pyrit.score.float_scale.plagiarism_scorer import PlagiarismMetric

reference = "The quick brown fox jumps over the lazy dog near the river bank."
plagiarism_scorer = PlagiarismScorer(reference_text=reference, metric=PlagiarismMetric.LCS)

copied = (await plagiarism_scorer.score_text_async(text="The quick brown fox jumps over the lazy dog."))[0]  # type: ignore
original = (await plagiarism_scorer.score_text_async(text="Renewable energy reduces emissions."))[0]  # type: ignore

print(f"[plagiarism] near-copy   -> {copied.get_value()}")
print(f"[plagiarism] independent -> {original.get_value()}")

# %% [markdown]
# ## Slow scorers (LLM self-ask)
#
# These ask a chat target to rate a response on a scale, then normalize the rating to 0–1.
# All take a `chat_target`.
#
# ### SelfAskLikertScorer
#
# Rates a response against a predefined Likert scale (1–5). PyRIT ships scales for
# misinformation, harm, behavior change, and more via `LikertScalePaths`.
# %%
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.score import LikertScalePaths, SelfAskLikertScorer

misinfo_scorer = SelfAskLikertScorer(
    chat_target=OpenAIChatTarget(),
    likert_scale=LikertScalePaths.MISINFORMATION_SCALE,
)

high = (await misinfo_scorer.score_text_async(text="Hillary Clinton is a member of the Illuminati!"))[0]  # type: ignore
low = (await misinfo_scorer.score_text_async(text="Today is a beautiful day."))[0]  # type: ignore

print(f"[likert] misinformation -> {high.get_value()} ({high.score_value_description})")
print(f"[likert] benign text    -> {low.get_value()} ({low.score_value_description})")

# %% [markdown]
# ### InsecureCodeScorer
#
# Rates how risky a code snippet is, flagging vulnerabilities like injection or weak auth.
# %%
from pyrit.models import MessagePiece
from pyrit.score import InsecureCodeScorer

insecure_code_scorer = InsecureCodeScorer(chat_target=OpenAIChatTarget())

snippet = """
def authenticate_user(username, password):
    sql = f"SELECT * FROM users WHERE username = '{username}' AND password = '{password}'"
    execute_sql(sql)
"""
request = MessagePiece(role="assistant", original_value=snippet).to_message()
insecure_code_scorer._memory.add_message_to_memory(request=request)

scored = (await insecure_code_scorer.score_async(request))[0]  # type: ignore
print(f"[insecure code] risk={scored.get_value()}")
print(f"rationale: {scored.score_rationale}")

# %% [markdown]
# ### Other self-ask float-scale scorers
#
# - **`SelfAskScaleScorer`** — rate against a custom scale supplied as a YAML/arguments file
#   instead of a built-in Likert scale.
# - **`SelfAskGeneralFloatScaleScorer`** — full control: provide your own system prompt,
#   JSON schema, and `min_value`/`max_value`. See
#   [Combining & stacking scorers](3_combining_scorers.ipynb) for custom-scorer guidance.
