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
# # Benchmark Scenario
#
# The benchmark scenario compares the effectiveness of multiple adversarial models in attaining an objective through various attack strategies.

# %%
import os

from pyrit.auth import get_azure_openai_auth
from pyrit.prompt_target import AzureMLChatTarget, OpenAIChatTarget
from pyrit.scenario.printer.console_printer import ConsoleScenarioResultPrinter
from pyrit.scenario.scenarios.benchmark import Benchmark
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import LoadDefaultDatasets

await initialize_pyrit_async(memory_db_type=IN_MEMORY, initializers=[LoadDefaultDatasets()])  # type: ignore

# Defaults to endpoint and api_key pulled from the AZURE_ML_MANAGED_ENDPOINT and AZURE_ML_KEY environment variables
gemma_adv = AzureMLChatTarget()
gemma_norm = AzureMLChatTarget(
    endpoint=os.environ.get("AZURE_ML_MANAGED_ENDPOINT_2"), api_key=os.environ.get("AZURE_ML_KEY_2")
)
adversarial_endpoint = os.environ["AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT2"]
gpt4o_adv = OpenAIChatTarget(
    endpoint=adversarial_endpoint,
    api_key=get_azure_openai_auth(adversarial_endpoint),
    model_name=os.environ["AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL2"],
    temperature=1.1,
)

benchmark_scenario = Benchmark(
    adversarial_models={
        "gemma_adv": gemma_adv,
        # "gemma_norm": gemma_norm,
        "gpt4o_adv": gpt4o_adv,
    }
)

await benchmark_scenario.initialize_async(  # type: ignore
    objective_target=OpenAIChatTarget(), max_concurrency=2
)

baseline_result = await benchmark_scenario.run_async()  # type: ignore
printer = ConsoleScenarioResultPrinter()

await printer.print_summary_async(baseline_result)  # type: ignore
