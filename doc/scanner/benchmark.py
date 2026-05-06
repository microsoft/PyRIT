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
# %load_ext autoreload
# %autoreload 2

# %%
import os

from pyrit.auth import get_azure_openai_auth
from pyrit.models import AttackOutcome
from pyrit.prompt_target import AzureMLChatTarget, OpenAIChatTarget
from pyrit.scenario.printer.console_printer import ConsoleScenarioResultPrinter
from pyrit.scenario.scenarios.benchmark import Benchmark
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import LoadDefaultDatasets

await initialize_pyrit_async(memory_db_type=IN_MEMORY, initializers=[LoadDefaultDatasets()])  # type: ignore

# Defaults to endpoint and api_key pulled from the AZURE_ML_MANAGED_ENDPOINT and AZURE_ML_KEY environment variables
gemma_adv = AzureMLChatTarget()

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
        "gpt4o_adv": gpt4o_adv,
    }
)

await benchmark_scenario.initialize_async(  # type: ignore
    objective_target=OpenAIChatTarget(), max_concurrency=2
)

baseline_result = await benchmark_scenario.run_async()  # type: ignore

# Resume handle: re-run with `Benchmark(..., scenario_result_id=<this id>)` to pick
# up where this run left off (constructor args must match the original run).
print(f"Scenario result id: {baseline_result.id}")

# ASR sensibility check: per-group rates should be in [0, 100], total > 0,
# and (when comparing models) at least some variance is expected.
_groups = baseline_result.get_display_groups()
_per_group = {
    label: int(sum(1 for r in rs if r.outcome == AttackOutcome.SUCCESS) / max(len(rs), 1) * 100)
    for label, rs in _groups.items()
}
_overall = baseline_result.objective_achieved_rate()
assert sum(len(rs) for rs in _groups.values()) > 0, "No attack results recorded"
assert all(0 <= rate <= 100 for rate in _per_group.values()), f"ASR out of bounds: {_per_group}"
print(f"ASR sanity: overall={_overall}%, per-model={_per_group}")

printer = ConsoleScenarioResultPrinter()

await printer.print_summary_async(baseline_result)  # type: ignore

# %% [markdown]
# ## Comparing Attack Techniques
#
# The first run used the default `light` strategy, which exercises a small subset
# of techniques.  To compare techniques head-to-head, we restrict the scenario to
# a hand-picked list and reuse the same two adversarial models (`gemma_adv` and
# `gpt4o_adv`) from the cell above.
#
# The per-technique × per-model breakdown lets us see which combinations are
# most effective against the objective target.

# %%
# Compare a hand-picked set of techniques against both adversarial models.
# Reuses gemma_adv and gpt4o_adv from the cell above so the comparison is
# isolated to the technique axis.
techniques_benchmark = Benchmark(
    adversarial_models={
        "gemma_adv": gemma_adv,
        "gpt4o_adv": gpt4o_adv,
    }
)

strategy_class = Benchmark.get_strategy_class()
selected_strategies = [
    strategy_class("role_play"),
    strategy_class("red_teaming"),
    strategy_class("context_compliance"),
]

await techniques_benchmark.initialize_async(  # type: ignore
    objective_target=OpenAIChatTarget(),
    scenario_strategies=selected_strategies,
    max_concurrency=2,
)

techniques_result = await techniques_benchmark.run_async()  # type: ignore

print(f"Scenario result id: {techniques_result.id}")

# ASR sensibility check: per-group rates should be in [0, 100] and we should
# have recorded at least one result.  Display groups are keyed by adversarial
# model label, so per-group ASR aggregates across the selected techniques.
_groups = techniques_result.get_display_groups()
_per_group = {
    label: int(sum(1 for r in rs if r.outcome == AttackOutcome.SUCCESS) / max(len(rs), 1) * 100)
    for label, rs in _groups.items()
}
_overall = techniques_result.objective_achieved_rate()
assert sum(len(rs) for rs in _groups.values()) > 0, "No attack results recorded"
assert all(0 <= rate <= 100 for rate in _per_group.values()), f"ASR out of bounds: {_per_group}"
print(f"ASR sanity: overall={_overall}%, per-model={_per_group}")

await printer.print_summary_async(techniques_result)  # type: ignore
