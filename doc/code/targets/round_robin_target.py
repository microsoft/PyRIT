# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.4
# ---

# %% [markdown]
# # Round Robin Target
#
# The `RoundRobinTarget` distributes requests across multiple inner targets using weighted round-robin
# selection. This is useful for load-balancing across multiple deployments of the same model (e.g.,
# Azure OpenAI endpoints in different regions) to avoid rate limits or spread cost.
#
# **Key considerations:**
# - All inner targets must be the same concrete class (e.g., all `OpenAIChatTarget`).
# - All inner targets must have identical TargetConfigurations (capabilities, policy, and normalization pipeline)
# - All inner targets must support multi-turn conversations and editable history.
# - Inner targets must have the same behavioral parameters (model, temperature, top_p) used for evaluation hashing. This allows
# users to evaluate round-robin targets for scoring and attack evaluation with confidence that results are comparable to using the
# inner targets directly.
# - Requests are distributed per-call, not per-conversation — any target can handle any turn.
# - Memory entries use the round-robin's identifier. The inner target that handled each
#   request is recorded in `prompt_metadata["inner_target_identifier"]`.
# - Optional integer weights control the distribution ratio.

# %% [markdown]
# ## Basic Usage
#
# In this example, we create two `OpenAIChatTarget` instances pointing to different endpoints
# (simulating two regional deployments of the same model) and wrap them in a `RoundRobinTarget`.
# We then send multiple prompts and show which inner target handled each one.

# %%
import os

from pyrit.auth import get_azure_openai_auth
from pyrit.models import Message
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import OpenAIChatTarget, RoundRobinTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

# Create two targets pointing to different regional deployments of the same model.
endpoint_a = os.environ["AZURE_OPENAI_GPT4O_ENDPOINT"]
endpoint_b = os.environ["AZURE_OPENAI_GPT4O_ENDPOINT2"]

target_a = OpenAIChatTarget(
    endpoint=endpoint_a,
    api_key=get_azure_openai_auth(endpoint_a),
    model_name=os.environ["AZURE_OPENAI_GPT4O_MODEL"],
    underlying_model=os.environ["AZURE_OPENAI_GPT4O_UNDERLYING_MODEL"],
)
target_b = OpenAIChatTarget(
    endpoint=endpoint_b,
    api_key=get_azure_openai_auth(endpoint_b),
    model_name=os.environ["AZURE_OPENAI_GPT4O_MODEL2"],
    underlying_model=os.environ["AZURE_OPENAI_GPT4O_UNDERLYING_MODEL2"],
)

# Wrap them in a RoundRobinTarget
rr_target = RoundRobinTarget(targets=[target_a, target_b])

# Send 4 prompts and observe the round-robin distribution
normalizer = PromptNormalizer()
prompts = [
    "What is 2 + 2?",
    "What color is the sky?",
    "Name a prime number.",
    "What is the capital of France?",
]

for i, prompt in enumerate(prompts):
    message = Message.from_prompt(prompt=prompt, role="user")
    response = await normalizer.send_prompt_async(message=message, target=rr_target)  # type: ignore

    # Show which inner target handled this request
    inner_hash = response.message_pieces[0].prompt_metadata.get("inner_target_identifier", "N/A")
    target_label = "Target A" if inner_hash == target_a.get_identifier().hash else "Target B"
    print(f"Prompt {i + 1}: '{prompt}' → handled by {target_label}")
    print(f"  Response: {response.message_pieces[0].converted_value[:80]}...")
    print()

# %% [markdown]
# ## Weighted Distribution
#
# You can pass `weights` to control the distribution ratio. For example, `weights=[2, 1]`
# sends roughly twice as many requests to the first target. This is useful when one
# deployment has higher rate limits or capacity.

# %%
await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

target_a = OpenAIChatTarget(
    endpoint=endpoint_a,
    api_key=get_azure_openai_auth(endpoint_a),
    model_name=os.environ["AZURE_OPENAI_GPT4O_MODEL"],
    underlying_model=os.environ["AZURE_OPENAI_GPT4O_UNDERLYING_MODEL"],
)
target_b = OpenAIChatTarget(
    endpoint=endpoint_b,
    api_key=get_azure_openai_auth(endpoint_b),
    model_name=os.environ["AZURE_OPENAI_GPT4O_MODEL2"],
    underlying_model=os.environ["AZURE_OPENAI_GPT4O_UNDERLYING_MODEL2"],
)

# Target A gets 2x the traffic
rr_weighted = RoundRobinTarget(targets=[target_a, target_b], weights=[2, 1])

normalizer = PromptNormalizer()
prompts = ["Prompt 1", "Prompt 2", "Prompt 3", "Prompt 4", "Prompt 5", "Prompt 6"]

target_a_hash = target_a.get_identifier().hash
counts = {"Target A": 0, "Target B": 0}

for prompt in prompts:
    message = Message.from_prompt(prompt=prompt, role="user")
    response = await normalizer.send_prompt_async(message=message, target=rr_weighted)  # type: ignore
    inner_hash = response.message_pieces[0].prompt_metadata.get("inner_target_identifier", "N/A")
    label = "Target A" if inner_hash == target_a_hash else "Target B"
    counts[label] += 1
    print(f"  '{prompt}' → {label}")

print(f"\nDistribution: Target A = {counts['Target A']}, Target B = {counts['Target B']}")

# %% [markdown]
# ## Multi-Turn Attack (Crescendo)
#
# The `RoundRobinTarget` works seamlessly with multi-turn attacks like Crescendo. Because
# round-robin targets require editable history, any inner target can reconstruct the full
# conversation from shared memory on each turn. This means different turns of the same
# conversation may be handled by different inner targets — true load-balancing even within
# a single multi-turn interaction.
#
# Note that using a `RoundRobinTarget` within a multi-turn attack can lead to greater API costs
# due to loss of prompt caching. For multi-turn attacks like Crescendo with many objectives, this
# can significantly increase API cost compared to pinning each conversation to a single target.
# This is a cost/latency vs. throughput trade-off — round-robin avoids per-endpoint rate limits at
# the expense of server-side caching. Users who need cache-efficient multi-turn conversations should
# assign individual targets at the attack or scenario level rather than using round-robin for those workloads.
#

# %%
from pyrit.executor.attack import AttackAdversarialConfig, CrescendoAttack
from pyrit.output import output_attack_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

# The objective target is a round-robin across two deployments
target_a = OpenAIChatTarget(
    endpoint=endpoint_a,
    api_key=get_azure_openai_auth(endpoint_a),
    model_name=os.environ["AZURE_OPENAI_GPT4O_MODEL"],
    underlying_model=os.environ["AZURE_OPENAI_GPT4O_UNDERLYING_MODEL"],
)
target_b = OpenAIChatTarget(
    endpoint=endpoint_b,
    api_key=get_azure_openai_auth(endpoint_b),
    model_name=os.environ["AZURE_OPENAI_GPT4O_MODEL2"],
    underlying_model=os.environ["AZURE_OPENAI_GPT4O_UNDERLYING_MODEL2"],
)
rr_target = RoundRobinTarget(targets=[target_a, target_b])

# The adversarial chat (used to generate attack prompts) can also be a round-robin,
# or a single target — it's independent of the objective target.
adversarial_chat = OpenAIChatTarget(
    endpoint=endpoint_a,
    api_key=get_azure_openai_auth(endpoint_a),
    model_name=os.environ["AZURE_OPENAI_GPT4O_MODEL"],
    underlying_model=os.environ["AZURE_OPENAI_GPT4O_UNDERLYING_MODEL"],
)
adversarial_config = AttackAdversarialConfig(target=adversarial_chat)

attack = CrescendoAttack(
    objective_target=rr_target,
    attack_adversarial_config=adversarial_config,
    max_turns=5,
    max_backtracks=3,
)

result = await attack.execute_async(  # type: ignore
    objective="Explain how to make a paper airplane that flies really far"
)
await output_attack_async(result)

# %% [markdown]
# ## Scoring Multiple Responses
#
# When using a `RoundRobinTarget` as the scorer's chat target, the scoring requests
# themselves are load-balanced across the inner targets. This is especially useful when
# scoring many responses in batch — the round-robin distributes the scoring LLM calls
# across deployments.

# %%
from pyrit.executor.attack import AttackExecutor, PromptSendingAttack
from pyrit.score import ContentClassifier, ContentClassifierPaths, SelfAskCategoryScorer

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

# Step 1: Generate some responses to score using a simple attack
objective_target = OpenAIChatTarget(
    endpoint=endpoint_a,
    api_key=get_azure_openai_auth(endpoint_a),
    model_name=os.environ["AZURE_OPENAI_GPT4O_MODEL"],
    underlying_model=os.environ["AZURE_OPENAI_GPT4O_UNDERLYING_MODEL"],
)
attack = PromptSendingAttack(objective_target=objective_target)

objectives = [
    "What is photosynthesis?",
    "Tell me a joke about cats.",
    "Explain how computers work.",
    "What is the meaning of life?",
]

results = await AttackExecutor().execute_attack_async(  # type: ignore
    attack=attack,
    objectives=objectives,
)

# Step 2: Score all responses using a round-robin scorer target
# The scorer's LLM calls are distributed across both targets
scorer_target_a = OpenAIChatTarget(
    endpoint=endpoint_a,
    api_key=get_azure_openai_auth(endpoint_a),
    model_name=os.environ["AZURE_OPENAI_GPT4O_MODEL"],
    underlying_model=os.environ["AZURE_OPENAI_GPT4O_UNDERLYING_MODEL"],
)
scorer_target_b = OpenAIChatTarget(
    endpoint=endpoint_b,
    api_key=get_azure_openai_auth(endpoint_b),
    model_name=os.environ["AZURE_OPENAI_GPT4O_MODEL2"],
    underlying_model=os.environ["AZURE_OPENAI_GPT4O_UNDERLYING_MODEL2"],
)
rr_scorer_target = RoundRobinTarget(targets=[scorer_target_a, scorer_target_b], weights=[2, 1])

scorer = SelfAskCategoryScorer.from_content_classifier(
    chat_target=rr_scorer_target,
    content_classifier=ContentClassifier.from_yaml(ContentClassifierPaths.HARMFUL_CONTENT_CLASSIFIER.value),
)

# Collect response messages for scoring
response_messages = [r.last_response.to_message() for r in results if r.last_response is not None]

scorer_target_a_hash = scorer_target_a.get_identifier().hash

# Score each response individually so we can track and print which scorer target handled it
# You may want to use `score_prompts_batch_async` like below in practice for efficiency
# await scorer.score_prompts_batch_async(messages=response_messages)  # type: ignore
for i, response_message in enumerate(response_messages):
    scores = await scorer.score_async(message=response_message)  # type: ignore

    # The scorer's internal LLM response has inner_target_identifier in metadata.
    # We can check the round-robin counter to determine which target was used.
    # Since set_system_prompt and send_prompt_async each call _next_target(),
    # the counter advances by 2 per scoring call (1 for system prompt, 1 for send).
    # We use the counter to show the alternation pattern.
    target_idx = rr_scorer_target._rotation[(rr_scorer_target._counter - 1) % len(rr_scorer_target._rotation)]
    scorer_label = "Scorer Target A" if target_idx == 0 else "Scorer Target B"

    for score in scores:
        print(
            f"Prompt {i + 1} scored by {scorer_label} | "
            f"Value: {score.get_value()} | "
            f"Category: {score.score_category} | "
            f"Rationale: {score.score_rationale[:60]}"
        )
