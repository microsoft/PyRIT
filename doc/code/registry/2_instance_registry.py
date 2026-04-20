# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.18.1
# ---

# %% [markdown]
# ## Why Instance Registries?
#
# Some components need configuration that can't easily be passed at instantiation time. For example, scorers often need:
# - A configured `chat_target` for LLM-based scoring
# - Specific prompt templates
# - Other dependencies
#
# Instance registries let initializers register fully-configured instances that are ready to use.

# %% [markdown]
# # Listing Available Instances
#
# Use `get_names()` to see registered instances, or `list_metadata()` for details.

# %%
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.registry import ScorerRegistry
from pyrit.score import SelfAskRefusalScorer
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

# Get the registry singleton
registry = ScorerRegistry.get_registry_singleton()

# Register a scorer instance for demonstration
chat_target = OpenAIChatTarget()
refusal_scorer = SelfAskRefusalScorer(chat_target=chat_target)
registry.register_instance(refusal_scorer)

# List what's available
names = registry.get_names()
print(f"Registered scorers: {names}")

# %% [markdown]
# ## Getting an Instance
#
# Use `get()` to retrieve a pre-configured instance by name. The instance is ready to use immediately.

# %%
# Get the first registered scorer
if names:
    scorer_name = names[0]
    scorer = registry.get(scorer_name)
    print(f"Retrieved scorer: {scorer}")
    print(f"Scorer type: {type(scorer).__name__}")

# %% [markdown]
# ## Inspecting Metadata
#
# Scorer metadata includes the scorer type and identifier for tracking.

# %%
from pyrit.score import ConsoleScorerPrinter

# Get metadata for all registered scorers
metadata = registry.list_metadata()
for item in metadata:
    print(f"\n{item.name}:")
    print(f"  Class: {item.class_name}")
    print(f"  Type: {item.scorer_type}")
    print(f"  Description: {item.description[:60]}...")

    ConsoleScorerPrinter().print_objective_scorer(scorer_identifier=item.scorer_identifier)

# %% [markdown]
# ## Filtering
#
# Use `list_metadata()` with `include_filters` and `exclude_filters` dictionaries to filter scorers by any metadata property. `include_filters` requires ALL criteria to match (AND logic). `exclude_filters` excludes items matching ANY criteria. Filters use exact match for simple types and membership check for list types.

# %%
# Filter by scorer_type (based on isinstance check against TrueFalseScorer/FloatScaleScorer)
true_false_scorers = registry.list_metadata(include_filters={"scorer_type": "true_false"})
print(f"True/False scorers: {[m.name for m in true_false_scorers]}")

# Filter by class_name
refusal_scorers = registry.list_metadata(include_filters={"class_name": "SelfAskRefusalScorer"})
print(f"Refusal scorers: {[m.name for m in refusal_scorers]}")

# Combine multiple filters (AND logic)
specific_scorers = registry.list_metadata(
    include_filters={"scorer_type": "true_false", "class_name": "SelfAskRefusalScorer"}
)
print(f"True/False refusal scorers: {[m.name for m in specific_scorers]}")
