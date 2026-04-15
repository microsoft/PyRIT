# ---
# jupyter:
#   jupytext:
#     cell_metadata_filter: -all
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.0
# ---

# %% [markdown]
# # HuggingFace Chat Target - optional
#
# This notebook is designed to demonstrate **instruction models** that use a **chat template**, allowing users to experiment with structured chat-based interactions.  Non-instruct models are excluded to ensure consistency and reliability in the chat-based interactions. More instruct models can be explored on Hugging Face.
#
# ## Key Points:
#
# 1. **Supported Instruction Models**:
#    - This notebook supports the following **instruct models** that follow a structured chat template. These are examples, and more instruct models are available on Hugging Face:
#      - `HuggingFaceTB/SmolLM-360M-Instruct`
#      - `microsoft/Phi-3-mini-4k-instruct`
#
#      - `...`
#
# 2. **Excluded Models**:
#    - Non-instruct models (e.g., `"google/gemma-2b"`, `"princeton-nlp/Sheared-LLaMA-1.3B-ShareGPT"`) are **not included** in this demo, as they do not follow the structured chat template required for the current local Hugging Face model support.
#
# 3. **Model Response Times**:
#    - The tests were conducted using a CPU, and the following are the average response times for each model:
#      - `HuggingFaceTB/SmolLM-1.7B-Instruct`: 5.87 seconds
#      - `HuggingFaceTB/SmolLM-135M-Instruct`: 3.09 seconds
#      - `HuggingFaceTB/SmolLM-360M-Instruct`: 3.31 seconds
#      - `microsoft/Phi-3-mini-4k-instruct`: 4.89 seconds
#      - `Qwen/Qwen2-0.5B-Instruct`: 1.38 seconds
#      - `Qwen/Qwen2-1.5B-Instruct`: 2.96 seconds
#      - `stabilityai/stablelm-2-zephyr-1_6b`: 5.31 seconds
#      - `stabilityai/stablelm-zephyr-3b`: 8.37 seconds
#

# %%
import time

from pyrit.executor.attack import (
    AttackExecutor,
    ConsoleAttackResultPrinter,
    PromptSendingAttack,
)
from pyrit.prompt_target import HuggingFaceChatTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

# models to test
model_id = "Qwen/Qwen2-0.5B-Instruct"

# List of prompts to send
prompt_list = ["What is 3*3? Give me the solution.", "What is 4*4? Give me the solution."]

# Dictionary to store average response times
model_times = {}

print(f"Running model: {model_id}")

try:
    # Initialize HuggingFaceChatTarget with the current model
    target = HuggingFaceChatTarget(model_id=model_id, use_cuda=False, tensor_format="pt", max_new_tokens=30)

    # Initialize the attack
    attack = PromptSendingAttack(objective_target=target)

    # Record start time
    start_time = time.time()

    # Send prompts asynchronously
    responses = await AttackExecutor().execute_attack_async(  # type: ignore
        attack=attack,
        objectives=prompt_list,
    )

    # Record end time
    end_time = time.time()

    # Calculate total and average response time
    total_time = end_time - start_time
    avg_time = total_time / len(prompt_list)
    model_times[model_id] = avg_time

    print(f"Average response time for {model_id}: {avg_time:.2f} seconds\n")

    # Print the conversations
    for result in responses:
        await ConsoleAttackResultPrinter().print_conversation_async(result=result)  # type: ignore

except Exception as e:
    print(f"An error occurred with model {model_id}: {e}\n")
    model_times[model_id] = None

# Print the model average time
if model_times[model_id] is not None:
    print(f"{model_id}: {model_times[model_id]:.2f} seconds")
else:
    print(f"{model_id}: Error occurred, no average time calculated.")
