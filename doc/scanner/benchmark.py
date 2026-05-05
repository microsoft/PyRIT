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
# ## Comparing Adversarial System Prompts
#
# `AttackAdversarialConfig` accepts a `system_prompt_path` that controls how the
# adversarial chat target frames its prompts.  By passing the *same* underlying
# target with *different* `system_prompt_path` values we can use `Benchmark` to
# compare the relative effectiveness of those prompts head-to-head.
#
# To isolate the system-prompt variable we restrict the run to `red_teaming`
# (the technique that directly consumes the adversarial config's
# `system_prompt_path`).  The three prompts below are bundled in PyRIT under
# `pyrit/datasets/executors/red_teaming/` — each frames the adversarial chat
# differently, so we expect the per-prompt ASR to vary.

# %%
from pathlib import Path

from pyrit.common.path import EXECUTOR_SEED_PROMPT_PATH
from pyrit.executor.attack import AttackAdversarialConfig

# Three adversarial system prompts shipped with PyRIT.  Same target (gpt4o_adv),
# only the system_prompt_path differs — so per-prompt ASR isolates the prompt's
# effect.
_RT_PROMPTS = Path(EXECUTOR_SEED_PROMPT_PATH) / "red_teaming"
prompt_paths = {
    "text_generation": _RT_PROMPTS / "text_generation.yaml",
    "violent_durian": _RT_PROMPTS / "violent_durian.yaml",
    "unethical_task": _RT_PROMPTS / "unethical_task_generation_prompt.yaml",
}

prompt_configs = {
    label: AttackAdversarialConfig(target=gpt4o_adv, system_prompt_path=path) for label, path in prompt_paths.items()
}

prompts_benchmark = Benchmark(adversarial_models=prompt_configs)

# Restrict to red_teaming so the comparison reflects the system prompt only.
red_teaming_strategy = Benchmark.get_strategy_class()("red_teaming")
await prompts_benchmark.initialize_async(  # type: ignore
    objective_target=OpenAIChatTarget(),
    scenario_strategies=[red_teaming_strategy],
    max_concurrency=2,
)

prompts_result = await prompts_benchmark.run_async()  # type: ignore

print(f"Scenario result id: {prompts_result.id}")

# ASR sensibility check + variance check (the whole point of a comparison).
_groups = prompts_result.get_display_groups()
_per_prompt = {
    label: int(sum(1 for r in rs if r.outcome == AttackOutcome.SUCCESS) / max(len(rs), 1) * 100)
    for label, rs in _groups.items()
}
_overall = prompts_result.objective_achieved_rate()
assert sum(len(rs) for rs in _groups.values()) > 0, "No attack results recorded"
assert all(0 <= rate <= 100 for rate in _per_prompt.values()), f"ASR out of bounds: {_per_prompt}"
assert len(set(_per_prompt.values())) > 1, (
    f"All prompts produced identical ASR ({_per_prompt}); comparison is not informative."
)
print(f"ASR sanity: overall={_overall}%, per-prompt={_per_prompt}")

await printer.print_summary_async(prompts_result)  # type: ignore
