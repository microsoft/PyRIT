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
# # Benchmark Scenarios
#
# Benchmark scenarios are a subset of scenarios that compare the effectiveness of attacks across an axis that varies within the scenario itself. The axis can be many things; currently, the only benchmark variant is the adversarial benchmark, whose axis of change is the adversarial model used in attacks.

# %% [markdown]
# ## Adversarial Benchmark
# The adversarial benchmarking scenario (`AdversarialBenchmark`) compares the effectiveness of different adversarial models in successfully executing attacks against a target model.

# %%
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario.printer.console_printer import ConsoleScenarioResultPrinter
from pyrit.scenario.scenarios.benchmark import AdversarialBenchmark
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import LoadDefaultDatasets

await initialize_pyrit_async(memory_db_type=IN_MEMORY, initializers=[LoadDefaultDatasets()])  # type: ignore

# Pass any number of adversarial PromptChatTargets as a list; AdversarialBenchmark
# infers a label for each from its identifier and runs every benchmark-friendly
# attack technique against the objective target with each adversarial model.
adversarial_model = OpenAIChatTarget()

benchmark_scenario = AdversarialBenchmark(adversarial_models=[adversarial_model])

await benchmark_scenario.initialize_async(  # type: ignore
    objective_target=OpenAIChatTarget(), max_concurrency=2
)

baseline_result = await benchmark_scenario.run_async()  # type: ignore

# Resume handle: re-run with `AdversarialBenchmark(..., scenario_result_id=<this id>)` to pick
# up where this run left off (constructor args must match the original run).
print(f"Scenario result id: {baseline_result.id}")

printer = ConsoleScenarioResultPrinter()

await printer.print_summary_async(baseline_result)  # type: ignore
