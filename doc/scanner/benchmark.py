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
# Benchmark scenarios compare attack effectiveness across an axis that varies within the scenario
# itself. The adversarial benchmark varies the **adversarial chat helper model** used in attacks. The
# decision robustness benchmark varies **meaning-preserving changes** to what a decision model judges.
# For full configuration options see
# `pyrit_scan --help` and the [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb).

# %% [markdown]
# ## Adversarial Benchmark
#
# `AdversarialBenchmark` holds the objective target and dataset constant and varies the adversarial
# chat model used to drive multi-turn attacks. Useful for evaluating which adversarial helper
# models produce stronger or weaker attack success rates against the same target.
#
# Adversarial targets are user-provided via the `adversarial_targets` scenario parameter. Each name
# must already be registered in `TargetRegistry` — typically by `TargetInitializer` from the
# `ADVERSARIAL_CHAT_*` env vars (see `.env_example`). Use `pyrit_scan list-targets` to see every
# target currently registered.
#
# ```bash
# pyrit_scan run benchmark.adversarial \
#   --initializers target \
#   --target openai_chat \
#   --adversarial-targets adversarial_chat \
#   --techniques role_play_video_game \
#   --max-dataset-size 1
# ```
#
# Pass multiple `--adversarial-targets` values to compare across models in a single run.
#
# **Default techniques:** `role_play_video_game`, `crescendo_simulated`, and `tap`. TAP's
# branching search makes this default slower and more expensive than the former `light` default.
# Every selected adversarial technique prepends one shared benchmark guidance layer to its native
# system prompt, including explicitly selected techniques such as `red_teaming`. The native strategy,
# template parameters, and response format remain the source of truth. Global registered factories
# and techniques used outside this benchmark remain unchanged.
# For a cheaper run, explicitly pass `--techniques light`.
#
# **Other available selections:** `light`, `single_turn`, `multi_turn`, plus one member per
# adversarial-capable source technique (e.g. `red_teaming`, `tap`, `crescendo_simulated`).

# %% [markdown]
# ## Setup

# %%
from pyrit.output import output_scenario_async
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.scenario import DatasetAttackConfiguration, DatasetSource
from pyrit.scenario.benchmark import AdversarialBenchmark
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.initializers import ScorerInitializer, TargetInitializer, TechniqueInitializer

await initialize_pyrit_async(  # type: ignore
    memory_db_type=IN_MEMORY,
    initializers=[TargetInitializer(), ScorerInitializer(), TechniqueInitializer()],
)

objective_target = OpenAIChatTarget()

# %%
from pyrit.scenario.benchmark import AdversarialBenchmarkTechnique

dataset_config = DatasetAttackConfiguration(sources=[DatasetSource(name="harmbench")], max_total=1)

scenario = AdversarialBenchmark()
scenario.set_params_from_args(
    args={
        "adversarial_targets": ["adversarial_chat"],
        "objective_target": objective_target,
        "scenario_techniques": [AdversarialBenchmarkTechnique.role_play_video_game],
        "dataset_config": dataset_config,
    }
)
await scenario.initialize_async()  # type: ignore

scenario_result = await scenario.run_async()  # type: ignore

# %%
await output_scenario_async(scenario_result)

# %% [markdown]
# ## Decision Robustness
#
# `DecisionRobustness` tests a decision model, such as one served through `LiteLLMDecisionTarget`, as the
# system under test. Each XSTest prompt becomes a two-option `choice` question: should an AI assistant
# help with this request (`allow`) or decline it (`block`)? Each request expects the wrong verdict, so the
# attack success rate is the error rate. The baseline sends the unchanged requests, and each technique
# makes one change that should not change the verdict:
#
# | Technique | Change |
# |---|---|
# | `base64`, `rot13`, `leet_speak`, `unicode_confusable`, `char_swap` | Rewrites the judged text. |
# | `injected_verdict` | Appends a note that claims the wrong verdict. |
# | `option_order` | Reverses the order of the two options. |
# | `option_name_swap` | Binds each option name to the other definition; the correct answer follows the definitions. |
#
# Results are split into `safe` requests, where errors are false blocks, and `unsafe` requests, where
# errors are missed harm. A technique is a weakness when its error rate rises above the baseline's.
# Prompts from other datasets need `expected_verdict` metadata of `allow` or `block`.
#
# ```bash
# pyrit_scan run benchmark.decision_robustness \
#   --initializers target \
#   --target microsoft_decision \
#   --max-dataset-size 20
# ```
#
# The run below uses Cloudflare's Clef-flash decision model through Workers AI. LiteLLM reads the
# account from `CLOUDFLARE_ACCOUNT_ID`.

# %%
import os

from pyrit.prompt_target import LiteLLMDecisionTarget
from pyrit.scenario.benchmark import DecisionRobustness, DecisionRobustnessDatasetConfiguration
from pyrit.scenario.core.dataset_configuration import DatasetFetchPolicy

decision_target = LiteLLMDecisionTarget(model_name="cloudflare/clef-flash", api_key=os.environ["CLOUDFLARE_API_KEY"])

decision_scenario = DecisionRobustness()
decision_scenario.set_params_from_args(
    args={
        "objective_target": decision_target,
        "dataset_config": DecisionRobustnessDatasetConfiguration(
            sources=[DatasetSource(name="xstest", fetch=DatasetFetchPolicy.IF_MISSING)],
            max_per_dataset="all",
            max_total=20,
        ),
    }
)
await decision_scenario.initialize_async()  # type: ignore

decision_result = await decision_scenario.run_async()  # type: ignore

# %%
await output_scenario_async(decision_result)
