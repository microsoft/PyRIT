# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.17.3
#   kernelspec:
#     display_name: pyrit-dev
#     language: python
#     name: python3
# ---

# %% [markdown]
# # 2. Resiliency and Retry
#
# PyRIT provides multiple layers of retry and resiliency mechanisms to handle failures gracefully during security testing. This notebook explains the different retry mechanisms, how they work together, and how to configure them for your use case.
#
# ## Overview: Different Levels of Retry Mechanisms
#
# PyRIT implements retries at multiple levels, each serving a different purpose:
#
# 1. Low-level Retries (e.g. `pyrit_target_retry`). These automatically attempt to retry certain target errors under known conditions. For example, if there is an HTTP `RateLimitError` on an LLM endpoint, the target can use this attribute to automatically make the HTTP request again with an exponential backoff.
# 2. Mid-level Retries (e.g. `pyrit_json_retry`). These automatically attempt to retry under known conditions. As an exmaple, if a scorer expects an LLM response to have specific JSON, and that is JSON malformed, the scorer can use this attribute to ask the LLM to try scoring again. This does not have an exponential backoff.
# 3. High-level/Scenario-Level Retries: High-level workflow that retries the entire scenario when anything goes wrong, including unknown exceptions. If you are sending thousands of prompts, this can happen! It has logic to pick up where it left off in the scenario execution.
#
# Understanding when and how to use each is key to building robust security testing workflows. It's also important to understand these retries are stacked on top of each other. For example, if a target is unresponsive for five requests (a low level retry), a higher level still counts that as a "success".

# %% [markdown]
# ## 1. Low-level Retries: API Call Resilience
#
# ### What is Target-Level Retry?
#
# The `pyrit_target_retry` decorator is a **low-level** retry mechanism that handles transient API failures when communicating with prompt targets (e.g., OpenAI, Azure, custom endpoints).
#
# ### What It Handles
#
# Target-level retry automatically retries when it encounters:
#
# - **Rate limit errors** (`RateLimitError`, `RateLimitException`): API rate limits exceeded
# - **Empty responses** (`EmptyResponseException`): Target returns no content
#
# Note if other unknown errors happen, there is no retry. For example, we don't generally want to retry 10 times if there is an auth failure :)
#
# ### Configuration
#
# Target-level retries are configured via environment variables:
#
# ```python
# RETRY_MAX_NUM_ATTEMPTS=10      # Maximum retry attempts (default: 10)
# RETRY_WAIT_MIN_SECONDS=5       # Minimum wait between retries (default: 5)
# RETRY_WAIT_MAX_SECONDS=220     # Maximum wait between retries (default: 220)
# ```
#
# The decorator uses **exponential backoff** - wait time increases exponentially with each retry attempt.
#
# ### When to Use
#
# Target-level retry is **automatically applied** to target implementations. You typically don't need to think about it - it's built into PyRIT's target infrastructure.
#
# It's most valuable when:
# - Working with rate-limited APIs (OpenAI, Azure, etc.)
# - Network instability causes occasional empty responses
# - You want automatic handling of transient communication failures

# %% [markdown]
# ## 2. Mid-level Retries: Response Format Resilience
#
# ### What is JSON-Level Retry?
#
# The `pyrit_json_retry` decorator handles failures when parsing JSON responses from targets. Due to the probabilistic nature of LLMs, they can occasionally return malformed JSON (or whatever format we're expecting).
#
# ### What It Handles
#
# JSON-level retry automatically retries when:
#
# - **Invalid JSON** (`InvalidJsonException`): Target returns non-parseable JSON
#
# ### Configuration
#
# JSON retries use the same environment variables as target retries:
#
# ```python
# RETRY_MAX_NUM_ATTEMPTS=10      # Maximum retry attempts
# ```
#
# One difference is it does not have an exponential backoff, it is usually retried immediately.
#
# ### When to Use
#
# Like target-level retry, JSON retry is **automatically applied** where needed. It's particularly useful when:
# - Working with targets that return structured data
# - LLMs occasionally generate malformed JSON
# - You need automatic recovery from parsing errors
#
#
# ## 3. Scenario-Level Retries: High-Level Workflow Resiliency
#
# ### What is Scenario-Level Retry?
#
# Scenario-level retry is the **highest-level** retry mechanism in PyRIT. When enabled via the `max_retries` parameter, it allows an entire scenario execution to automatically retry if an exception occurs during the workflow.
#
# But also note, if you rerun the same scenario manually, that follows the same logic and is always an option.
#
# ### Key Features
#
# - **Picks up where it left off**: On retry, the scenario skips already-completed objectives and continues from the point of exception
# - **Broad exception handling**: Catches any exception during scenario execution (network issues, target failures, scoring errors, etc.)
# - **Configurable attempts**: Set `max_retries` to control how many additional attempts are allowed
# - **Progress tracking**: The `number_tries` field in `ScenarioResult` tracks total attempts
#
# ### How It Works
#
# When you call `scenario.run_async()`, PyRIT:
#
# 1. **Initial Attempt**: Executes all atomic attacks in sequence
# 2. **On Exception**: If an exception occurs, checks if retries remain
# 3. **Retry with Resume**: On retry, queries memory to identify completed objectives and skips them
# 4. **Continue from Exception Point**: Executes only the remaining objectives
# 5. **Repeat**: Continues retrying until success or `max_retries` exhausted
#
# ### Example: Basic Scenario with Retries

# %%
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario.foundry import FoundryStrategy, RedTeamAgent
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import LoadDefaultDatasets

await initialize_pyrit_async(memory_db_type=IN_MEMORY, initializers=[LoadDefaultDatasets()])  # type: ignore

objective_target = OpenAIChatTarget()

# Create a scenario with retry configuration
scenario = RedTeamAgent()

await scenario.initialize_async(  # type: ignore
    objective_target=objective_target,
    max_concurrency=5,
    max_retries=3,
    scenario_strategies=[FoundryStrategy.Base64],
)

# Execute with automatic retry after exceptions
result = await scenario.run_async()  # type: ignore

print(f"Scenario completed after {result.number_tries} attempt(s)")
print(f"Total results: {len(result.attack_results)}")

# %% [markdown]
#
# ### Configuring max_retries
#
# The `max_retries` parameter controls how many **additional attempts** are allowed after the initial attempt:
#
# - `max_retries=0` (default): No retries - fail immediately on first error
# - `max_retries=1`: 2 total attempts (1 initial + 1 retry)
# - `max_retries=3`: 4 total attempts (1 initial + 3 retries)
#
# **Formula**: `total_attempts = 1 + max_retries`
#
# ### When to Use Scenario-Level Retries
#
# Use scenario-level retries when:
#
# - ✅ Running long-duration test campaigns that might encounter transient exceptions
# - ✅ Testing against unreliable targets or networks
# - ✅ You want to ensure comprehensive test coverage despite intermittent issues
# - ✅ You need workflow-level resilience (e.g., partial completion + retry)
#
# Don't use scenario-level retries when:
#
# - ❌ You want immediate failure feedback for debugging
# - ❌ Testing configurations where retries would mask real issues
# - ❌ Cost-sensitive environments (retries consume additional API calls)

# %% [markdown]
# ### Manual Scenario Resumption
#
# In addition to automatic retries, you can manually resume a scenario by calling `run_async()` again. PyRIT will automatically pick up where it left off, skipping completed objectives:
#
# ```python
# # First attempt - may fail partway through
# try:
#     result = await scenario.run_async()  # type: ignore
# except Exception as e:
#     print(f"Scenario failed: {e}")
#
# # Simply call run_async() again - resumes from where it left off
# result = await scenario.run_async()  # type: ignore
# ```
#
# To resume in a different session, pass the `scenario_result_id` when creating a new scenario instance:
#
# ```python
# # Save the ID from the first run
# scenario_id = str(result.id)
#
# # Later, create a new scenario with the same configuration and the saved ID
# resumed_scenario = RedTeamAgent(
#     objective_target=objective_target,
#     scenario_strategies=[FoundryStrategy.Base64],
#     scenario_result_id=scenario_id,  # Resume from this scenario
# )
# await resumed_scenario.initialize_async()  # type: ignore
# result = await resumed_scenario.run_async()  # type: ignore  # Picks up where it left off
# ```
#
# **Note:** The scenario configuration (strategies, target type, etc.) must match the original for resumption to work.

# %% [markdown]
# ### Resume from Partial Completion
#
# One of the most powerful features of scenario-level retry is **resumption**. When a scenario raises an exception partway through:
#
# 1. **Completed objectives are saved** to memory before the exception
# 2. **On retry**, PyRIT queries memory to find completed objectives
# 3. **Skips completed work** and continues from the point of exception
# 4. **No duplicate execution** of already-successful tests
#
# This is particularly valuable for:
# - Large test suites with hundreds of objectives
# - Expensive API calls where re-execution would be costly
# - Long-running campaigns that might encounter transient infrastructure issues
#
# #### Example Scenario
#
# Imagine a scenario with 100 objectives:
# - First attempt completes 60 objectives successfully
# - Objective 61 fails due to network timeout
# - On retry: PyRIT skips objectives 1-60 and resumes from objective 61
# - No wasted work - only the remaining 40 objectives are attempted

# %% [markdown]
# ## Understanding the Retry Hierarchy
#
# The five retry mechanisms work at different levels, with three core mechanisms handling most scenarios:
#
# ### Core Retry Mechanisms
#
# ```
# ┌─────────────────────────────────────────────────────────────┐
# │ Scenario-Level Retry (max_retries)                          │
# │ • Handles ANY exception in the entire workflow              │
# │ • Resumes from point of exception                           │
# │ • Configurable per scenario                                 │
# │                                                             │
# │  ┌───────────────────────────────────────────────────────┐  │
# │  │ AtomicAttack Execution                                │  │
# │  │                                                       │  │
# │  │  ┌─────────────────────────────────────────────────┐  │  │
# │  │  │ JSON-Level Retry (pyrit_json_retry)             │  │  │
# │  │  │ • Handles invalid JSON responses                │  │  │
# │  │  │ • No exponential backoff (immediate retry)      │  │  │
# │  │  │ • Uses target call, so includes target retry    │  │  │
# │  │  │                                                 │  │  │
# │  │  │  ┌──────────────────────────────────────────┐   │  │  │
# │  │  │  │ Target-Level Retry (pyrit_target_retry)  │   │  │  │
# │  │  │  │ • Handles rate limits, empty responses   │   │  │  │
# │  │  │  │ • Exponential backoff                    │   │  │  │
# │  │  │  │ • Configured via environment variables   │   │  │  │
# │  │  │  └──────────────────────────────────────────┘   │  │  │
# │  │  └─────────────────────────────────────────────────┘  │  │
# │  └───────────────────────────────────────────────────────┘  │
# └─────────────────────────────────────────────────────────────┘
# ```
#
# ### How They Work Together
#
# These mechanisms form a **defense in depth** strategy:
#
# 1. **Target retry** handles transient API failures with exponential backoff
# 2. **JSON retry** wraps target calls and retries immediately if JSON is malformed (which then uses target retry for the actual call)
# 3. If retries exhaust their attempts, the exception bubbles up
# 4. **Scenario retry** catches it and retries the entire workflow
# 5. On scenario retry, target/JSON retries get a fresh set of attempts

# %% [markdown]
# ## Best Practices
#
# ### 1. Start Conservative for Scenario retries
#
# For scenarios, begin with `max_retries=0` during development to catch issues quickly.
#
# ```python
# # Development: fail fast
# dev_scenario = RedTeamAgent(
#     objective_target=target,
#     max_retries=0,  # No retries - see failures immediately
# )
#
# # Production: resilient execution
# prod_scenario = RedTeamAgent(
#     objective_target=target,
#     max_retries=3,  # Automatic retry after transient exceptions
# )
# ```
#
# At the scenario level, remember you are retrying any exception and all low-level retries have already happened. This can be useful since unknown transient exceptions can happen. However, be cautious with this.
#
# ### 2. For anything lower than a Scenario, only retry with known cases
#
# When implementing retry logic at the target or JSON level, **only retry for specific, known failure conditions**. Don't catch and retry all exceptions blindly.
#
# **Why this matters:**
# - **Unknown errors shouldn't be retried**: If you get an authentication error, permission denied, or invalid configuration, retrying won't help and wastes time
# - **Known transient errors should be retried**: Rate limits, network timeouts, and temporary service unavailability are good candidates for retry
# - **Fail fast for real issues**: Unknown exceptions likely indicate bugs or configuration problems that need immediate attention
#
# ### 3. Log Analysis
#
# PyRIT logs retry attempts at ERROR level. Monitor these logs to identify patterns:
#
# ```python
# import logging
#
# # Enable detailed logging
# logging.basicConfig(level=logging.INFO)
#
# # Look for patterns like:
# # ERROR - Scenario 'Test' failed on attempt 1 ... Retrying... (2 retries remaining)
# ```
