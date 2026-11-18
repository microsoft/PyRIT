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
# # Common Scenario Parameters
#
# This guide covers the key parameters for configuring scenarios programmatically: datasets,
# techniques, baseline execution, and custom scorers. All examples use `RedTeamAgent` but the
# patterns apply to any scenario. The last section,
# [Reporting Every Conversation](#reporting-every-conversation), shows how to print each attack's
# conversation and save a full HTML report.
#
# > **Two selection axes**: *Techniques* select attack techniques (*how* attacks run — e.g., prompt
# > sending, role play, TAP). *Datasets* select objectives (*what* is tested — e.g., harm categories,
# > compliance topics). Use `--datasets` on the CLI to filter by content category.
#
# > **Running scenarios from the command line?** See the [Scanner documentation](../../scanner/0_scanner.md).
#
# ## Setup
#
# Initialize PyRIT and create the target we want to test.

# %%
from pathlib import Path

from pyrit.output import output_scenario_async
from pyrit.registry import TargetRegistry
from pyrit.scenario.foundry import FoundryTechnique, RedTeamAgent
from pyrit.setup import initialize_from_config_async

await initialize_from_config_async(config_path=Path("../../scanner/pyrit_conf.yaml"))  # type: ignore

objective_target = TargetRegistry.get_registry_singleton().instances.get("openai_chat")
# %% [markdown]
# ## Dataset Configuration
#
# `DatasetAttackConfiguration` controls which prompts (objectives) are sent to the target.
# Use `sources` to select named datasets from memory.
# By default, `RedTeamAgent` loads four random objectives from HarmBench [@mazeika2024harmbench].

# %%
from pyrit.scenario import DatasetAttackConfiguration, DatasetSource

dataset_config = DatasetAttackConfiguration(sources=[DatasetSource(name="harmbench")], max_total=2)

# %% [markdown]
# For more control, use `SeedDatasetProvider` to fetch datasets and pass explicit `seed_groups`.
# This is useful when you need to filter, combine, or inspect the prompts before running.
#
# The default techniques below include `AsciiSmugglerConverter`, which only accepts printable
# ASCII (0x20-0x7E). Filter out seed groups containing newlines, other control characters, or
# non-ASCII text before sampling. This preserves the original text of the selected objectives.

# %%
from pyrit.datasets import SeedDatasetProvider
from pyrit.models import SeedGroup

datasets = await SeedDatasetProvider.fetch_datasets_async(dataset_names=["harmbench"])  # type: ignore
seed_groups: list[SeedGroup] = [
    group
    for group in datasets[0].seed_groups
    if all(seed.value.isascii() and seed.value.isprintable() for seed in group.seeds)
]

# Pass explicit seed_groups instead of named sources
dataset_config = DatasetAttackConfiguration(seed_groups=seed_groups, max_total=2)

# %% [markdown]
# ## Technique Selection and Composition
#
# `FoundryTechnique` is an enum that defines which attack techniques the scenario runs. There are
# three ways to specify techniques:
#
# **Individual techniques** — a single converter or multi-turn attack:

# %%
single_technique = [FoundryTechnique.Base64]

# %% [markdown]
# **Aggregate techniques** — tag-based groups that expand to all matching techniques. For example,
# `EASY` expands to all techniques tagged as easy (Base64, Binary, CharSwap, etc.):

# %%
aggregate_technique = [FoundryTechnique.EASY]

# %% [markdown]
# **Composite techniques** — pair an attack with one or more converters using `FoundryComposite`.
# For example, to run Crescendo with Base64 encoding applied:

# %%
from pyrit.scenario.foundry import FoundryComposite

composite_technique = [FoundryComposite(attack=FoundryTechnique.Crescendo, converters=[FoundryTechnique.Base64])]

# %% [markdown]
# You can mix all three types in a single list:

# %%
scenario_techniques = [
    FoundryTechnique.Base64,
    FoundryTechnique.Binary,
    FoundryComposite(attack=FoundryTechnique.Crescendo, converters=[FoundryTechnique.Caesar]),
]

# %% [markdown]
# ## Baseline Execution
#
# The baseline sends each objective directly to the target without any converters or multi-turn
# techniques. It is included automatically when `include_baseline=True` (the default for
# scenarios that support a baseline). This is useful for:
#
# - **Measuring default defenses** — how does the target respond to unmodified harmful prompts?
# - **Establishing comparison points** — compare baseline refusal rates against attack-enhanced runs
# - **Calculating attack lift** — how much does each technique improve over the baseline?

# %%
baseline_scenario = RedTeamAgent()
baseline_scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": None,  # Uses default techniques; baseline is prepended automatically
        "dataset_config": dataset_config,
    }
)
await baseline_scenario.initialize_async()  # type: ignore
baseline_result = await baseline_scenario.run_async()  # type: ignore
await output_scenario_async(baseline_result)  # type: ignore [top-level-await]

# %% [markdown]
# ### Sorting the Per-Group Breakdown by Success Rate
#
# By default, the **Per-Group Breakdown** lists groups in the order they were executed. The baseline
# run above produces a row for every default technique, which makes it hard to spot the most
# successful ones at a glance. Pass `sort_groups_by_success_rate=True` to `output_scenario_async` to
# re-render the same result with the highest success rates at the top (groups with equal rates keep
# their original relative order):

# %%
await output_scenario_async(baseline_result, sort_groups_by_success_rate=True)

# %% [markdown]
# To disable the automatic baseline entirely (e.g., when you only want attack techniques with no
# comparison), set `include_baseline=False` in the run params:
#
# ```python
# scenario = RedTeamAgent()
# scenario.set_params_from_args(
#     args={
#         "objective_target": objective_target,
#         "scenario_techniques": [FoundryTechnique.Base64],
#         "include_baseline": False,
#     }
# )
# await scenario.initialize_async()
# ```

# %% [markdown]
# ## Incompatible techniques
#
# Set `incompatible_technique_policy` to `skip` (default) to warn and omit incompatible
# techniques, or `raise` to stop initialization.
#
# ```python
# scenario = RedTeamAgent()
# scenario.set_params_from_args(
#     args={"objective_target": objective_target, "incompatible_technique_policy": "raise"}
# )
# await scenario.initialize_async()
# ```
#
# Use `--incompatible-technique-policy raise` on the CLI or
# `incompatible_technique_policy: raise` in YAML. `None` uses the scenario class default.
#
# This policy does not enable adaptation. Initialization fails if no selected technique can run,
# even with a baseline. Other construction errors are not skipped.

# %% [markdown]
# ## Custom Scorers
#
# By default, `RedTeamAgent` uses a composite scorer with Azure Content Filter and SelfAsk Refusal
# scorers. You can override this by passing your own `AttackScoringConfig` with a custom
# `objective_scorer`.
#
# For example, to use an inverted refusal scorer (where "True" means the target refused):

# %%
from pyrit.executor.attack import AttackScoringConfig
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.score import SelfAskRefusalScorer, TrueFalseInverterScorer

refusal_scorer = SelfAskRefusalScorer(chat_target=OpenAIChatTarget())
inverted_scorer = TrueFalseInverterScorer(scorer=refusal_scorer)

custom_scenario = RedTeamAgent(
    attack_scoring_config=AttackScoringConfig(objective_scorer=inverted_scorer),
)
custom_scenario.set_params_from_args(  # type: ignore
    args={
        "objective_target": objective_target,
        "scenario_techniques": [FoundryTechnique.Base64],
        "dataset_config": dataset_config,
    }
)
await custom_scenario.initialize_async()  # type: ignore

custom_result = await custom_scenario.run_async()  # type: ignore
await output_scenario_async(custom_result)

# %% [markdown]
# ## Judging Each Dataset With Its Own Scorer
#
# Some runs need a different judge per dataset, such as a hate-speech scorer for one harm category
# and a violence scorer for another. `RapidResponse`, `Cyber`, and `Leakage` accept a
# `CompatibleScorers` pool in place of one scorer. Bind each candidate to an evaluation key, then
# name the key a dataset needs on its `DatasetSource`:
#
# ```python
# from pyrit.scenario import DatasetAttackConfiguration, DatasetSource
# from pyrit.scenario.airt import RapidResponse
# from pyrit.score import CompatibleScorers, ScorerCandidate, ScorerRequirements
#
# scorers = CompatibleScorers(
#     candidates=[
#         ScorerCandidate(scorer=hate_scorer, evaluation="harm.hate"),
#         ScorerCandidate(scorer=violence_scorer, evaluation="harm.violence"),
#     ]
# )
# dataset_config = DatasetAttackConfiguration(
#     sources=[
#         DatasetSource(name="airt_hate", scorer_requirements=ScorerRequirements(evaluation="harm.hate")),
#         DatasetSource(name="airt_violence", scorer_requirements=ScorerRequirements(evaluation="harm.violence")),
#     ]
# )
# scenario = RapidResponse(objective_scorer=scorers)
# scenario.set_params_from_args(args={"objective_target": objective_target, "dataset_config": dataset_config})
# await scenario.initialize_async()
# ```
#
# Selection runs once per technique and dataset while the scenario initializes, and it never calls a
# judge model. A candidate qualifies when it accepts the evidence the attack produces, carries the
# required evaluation key, meets the technique's own scorer needs, and accepts the conditions of
# every objective in the dataset. The first qualifying candidate in pool order is used, so order
# only matters between candidates that both qualify. A dataset with no requirements uses the first
# candidate that can judge its objectives.
#
# When no candidate qualifies, `incompatible_technique_policy` applies to that technique and dataset:
# `skip` leaves it out with a warning, and `raise` stops with each candidate's reason. A required key
# that no candidate is bound to, such as a typo, stops initialization and lists the keys the pool has.
# With a baseline, each dataset gets its own baseline judged by the same scorer as its attacks. The
# run plan records every selection, and a resumed run stops if the pool or the objectives' scoring
# criteria have changed since it started.

# %% [markdown]
# (reporting-every-conversation)=
# ## Reporting Every Conversation
#
# `output_scenario_async` gives a high-level overview of a run. To see granular details within each attack, use
# `output_scenario_conversations_async`. It reads each attack's conversation from memory and prints
# one JSON document that shows only the objective score on each response, like
# `pyrit_scan scenario-results --view conversations`. It includes every attack unless you pass
# `attack_result_ids` or `limit`; here `limit=1` prints just the first one.

# %%
from pyrit.output import output_scenario_conversations_async

await output_scenario_conversations_async(custom_result, limit=1)

# %% [markdown]
# `output_scenario_full_async` adds the run overview, like `--view full`, and writes the report as
# JSON or a standalone HTML page. HTML needs a sink such as `FileSink`. This example writes the report
# to a temporary folder; point `FileSink` at your own path to keep it.

# %%
import tempfile

from pyrit.output import FileSink, output_scenario_full_async

with tempfile.TemporaryDirectory() as report_dir:
    report_path = Path(report_dir) / "report.html"
    await output_scenario_full_async(custom_result, format="html", sink=FileSink(path=report_path))
    print(f"Wrote {report_path.name}")
