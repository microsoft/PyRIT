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
# # Scoring
# %% [markdown]
# Scoring evaluates what happened to a prompt. It is how PyRIT answers questions like:
#
# - Was prompt injection detected?
# - Was the prompt blocked? Why?
# - Was there harmful content in the response? How bad was it?
#
# A scorer takes a response (or a whole conversation) and returns one or more
# [`Score`](../../../pyrit/models/score.py) objects. Scorers are used three ways:
# directly (this page), automatically inside an [attack](../executor/1_single_turn.ipynb#prompt-sending),
# and over many stored responses with the [batch scorer](#batch-scoring).
#
# ## The two return types
#
# Every concrete scorer returns one of two score types:
#
# - **`true_false`** — a boolean. Good for success criteria ("did the attack succeed?"),
#   refusal detection, and policy checks. `score.get_value()` returns a `bool`.
# - **`float_scale`** — a number normalized to `0.0`–`1.0`. Good for quantifying *how much*
#   of something is present (e.g. severity of harmful content). `score.get_value()` returns a `float`.
#
# The two are convertible: a `float_scale` score becomes `true_false` by applying a
# threshold (see [Combining & stacking scorers](3_combining_scorers.ipynb)).
# %% [markdown]
# ## Scorer reference table
#
# Every concrete scorer, grouped by return type. The table is generated from
# `get_scorer_info()`, which inspects each scorer class without instantiating it.
# %%
import pandas as pd

from pyrit.score import get_scorer_info
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY, silent=True)  # type: ignore

rows = [
    {
        "Scorer": info.name,
        "Return type": info.score_type,
        "Uses LLM?": "yes" if info.uses_llm else "no",
    }
    for info in get_scorer_info()
]

df = pd.DataFrame(rows)
pd.set_option("display.max_rows", None)
print(df.to_string(index=False))

# %% [markdown]
# ## The class hierarchy
#
# `Scorer` separates the evidence to inspect from the result family. `TrueFalseScorer` and
# `FloatScaleScorer` define the two result families. `MessageScorer` adds message resolution
# and message-only policy. Most built-in scorers combine one result-family base with
# `MessageScorer`.

# %% [markdown] class="col-page-right"
#
# ```mermaid
# classDiagram
#     class Scorer { <<abstract>> }
#     class MessageScorer { <<abstract>> }
#     class FloatScaleScorer { <<abstract>> }
#     class TrueFalseScorer { <<abstract>> }
#     class MessageFloatScaleScorer { <<abstract>> }
#     class MessageTrueFalseScorer { <<abstract>> }
#     class ConversationScorer { <<abstract>> }
#
#     Scorer <|-- MessageScorer
#     Scorer <|-- FloatScaleScorer
#     Scorer <|-- TrueFalseScorer
#     MessageScorer <|-- MessageFloatScaleScorer
#     FloatScaleScorer <|-- MessageFloatScaleScorer
#     MessageScorer <|-- MessageTrueFalseScorer
#     TrueFalseScorer <|-- MessageTrueFalseScorer
#     MessageScorer <|-- ConversationScorer
#
#     MessageFloatScaleScorer <|-- AzureContentFilterScorer
#     MessageFloatScaleScorer <|-- SelfAskLikertScorer
#     MessageFloatScaleScorer <|-- SelfAskScaleScorer
#     MessageFloatScaleScorer <|-- InsecureCodeScorer
#
#     MessageTrueFalseScorer <|-- SubStringScorer
#     MessageTrueFalseScorer <|-- RegexScorer
#     MessageTrueFalseScorer <|-- SelfAskRefusalScorer
#     MessageTrueFalseScorer <|-- SelfAskCategoryScorer
#     TrueFalseScorer <|-- TrueFalseCompositeScorer
#     TrueFalseScorer <|-- FloatScaleThresholdScorer
# ```

# %% [markdown]
#
# `ConversationScorer` is never instantiated directly. `create_conversation_scorer()`
# accepts a `MessageTrueFalseScorer` or `MessageFloatScaleScorer` and builds a compatible
# subclass that evaluates a whole conversation.
#
# Generic family scorers consume a `Scorable` without assuming that it resolves to a
# message. Message scorers also support message-specific entry points and policy. Generic
# wrappers do not inherit those message APIs from their children; use their canonical
# `score_async(scorable=..., expectation=...)` entry point. See
# [Combining & stacking scorers](3_combining_scorers.ipynb).
# %% [markdown]
# ## Evidence and score status
#
# A `Scorable` identifies what a scorer evaluates. `MessageScorable` refers to message pieces
# in memory. `ContentScorable` carries loose text or media. When a file-backed
# `ContentScorable` is persisted with a score, PyRIT copies the file to configured results
# storage and stores its SHA-256 digest. The score remains resolvable after the source file is
# removed.
#
# A complete score has `status="complete"` and a typed value. An undetermined score has
# `status="undetermined"` and no value. A fully blocked response is a complete negative result
# by default: `False` for message true/false scorers and `0.0` for message float-scale scorers.
# `SelfAskRefusalScorer` is the intentional exception because a content-filter block is a
# refusal, so it returns `True`. Other response errors remain undetermined.
# %% [markdown]
# ## Scoring directly
#
# The smallest example: a local `SubStringScorer` (a `true_false` scorer) over a string.
# No model call, no credentials.
# %%
from pyrit.score import SubStringScorer

scorer = SubStringScorer(substring="I hate", categories=["hate"])

flagged = (await scorer.score_text_async(text="I hate you."))[0]  # type: ignore
clean = (await scorer.score_text_async(text="Have a nice day."))[0]  # type: ignore

print(f"'I hate you.' -> {flagged.get_value()}")
print(f"'Have a nice day.' -> {clean.get_value()}")

# %% [markdown]
# ## Scoring inside an attack
#
# Most of the time a scorer is handed to an attack as the **objective scorer** that decides
# whether the objective was met. Here a `SubStringScorer` and a local `TextTarget` keep the
# example offline. `TextTarget` just records the prompt and returns no assistant content, so
# there is nothing substantive for the scorer to flag — swap in an LLM-backed
# [target](../targets/0_prompt_targets.md) (and any scorer) to score a real model response.
# %%
from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.output import output_attack_async
from pyrit.prompt_target import TextTarget

attack = PromptSendingAttack(
    objective_target=TextTarget(),
    attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
)

result = await attack.execute_async(objective="Say something hateful")  # type: ignore
await output_attack_async(result)

# %% [markdown]
# ## Batch scoring
#
# `BatchScorer` scores responses already in memory — for example everything an attack sent.
# It runs in parallel and can select responses by conversation, prompt id, memory labels,
# timestamps, and more. It works with any scorer; here we reuse the local `SubStringScorer`.
# %%
from pyrit.executor.attack import AttackExecutor
from pyrit.memory import CentralMemory
from pyrit.score import BatchScorer

prompts = ["I hate mondays.", "What a lovely morning.", "I hate waiting in line."]

results = await AttackExecutor().execute_attack_async(  # type: ignore
    attack=PromptSendingAttack(objective_target=TextTarget()),
    objectives=prompts,
)

memory = CentralMemory.get_memory_instance()
prompt_ids = []
for r in results:
    prompt_ids.extend(str(p.id) for p in memory.get_message_pieces(conversation_id=r.conversation_id))

batch_scorer = BatchScorer()
scores = await batch_scorer.score_responses_by_filters_async(scorer=scorer, prompt_ids=prompt_ids)  # type: ignore

for score in scores:
    text = memory.get_message_pieces(prompt_ids=[str(score.message_piece_id)])[0].original_value
    print(f"{score.get_value()} : {text}")
