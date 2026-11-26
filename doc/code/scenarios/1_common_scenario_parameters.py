# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.6
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
# ### Reading Attack and Baseline Summaries
#
# The overall attack rate excludes groups explicitly marked as baselines in the saved run plan.
# Baseline results retain their own counts and rate, and all groups remain in the breakdown.
# For a baseline-only result, the attack rate is undefined: `N/A` in text/HTML and `null` in JSON,
# not 0% failed attacks.
#
# The following synthetic recorded results demonstrate both cases without initializing targets,
# calling models, or reading memory. This cell can also run independently of the live examples above.
# These are reporting examples, not measured model performance.

# %%
from pyrit.common.utils import to_sha256
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AttackOutcome,
    AttackResult,
    ScenarioIdentifier,
    ScenarioResult,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanGroupKind,
    ScenarioRunPlanSeedGroup,
)
from pyrit.output.scenario_result.json import JsonScenarioResultPrinter
from pyrit.output.scenario_result.pretty import PrettyScenarioResultPrinter

objective = "Synthetic reporting example"
summary_plan = ScenarioRunPlan(
    seed_groups=[ScenarioRunPlanSeedGroup(id="seed", objective=objective, objective_sha256=to_sha256(objective))],
    atomic_groups=[
        ScenarioRunPlanAtomicGroup(
            id=kind.value,
            atomic_attack_name=kind.value,
            display_group=kind.value,
            technique_eval_hash=kind.value,
            seed_group_ids=["seed"],
            kind=kind,
        )
        for kind in (ScenarioRunPlanGroupKind.ATTACK, ScenarioRunPlanGroupKind.BASELINE)
    ],
)
summary_result = ScenarioResult(
    scenario_identifier=ScenarioIdentifier(class_name="SyntheticSummary", class_module="example", version=1),
    metadata={SCENARIO_RUN_PLAN_METADATA_KEY: summary_plan.model_dump(mode="json")},
    attack_results={
        kind: [
            AttackResult(
                conversation_id=f"synthetic-{kind}",
                objective=objective,
                outcome=outcome,
                attribution_data={"parent_collection": kind, "parent_eval_hash": kind, "seed_group_id": "seed"},
            )
        ]
        for kind, outcome in (("attack", AttackOutcome.SUCCESS), ("baseline", AttackOutcome.FAILURE))
    },
)
baseline_only_plan = summary_plan.model_copy(update={"atomic_groups": [summary_plan.atomic_groups[1]]})
baseline_only_result = summary_result.model_copy(
    update={
        "attack_results": {"baseline": summary_result.attack_results["baseline"]},
        "metadata": {SCENARIO_RUN_PLAN_METADATA_KEY: baseline_only_plan.model_dump(mode="json")},
    }
)
for label, recorded_result in (("Mixed", summary_result), ("Baseline only", baseline_only_result)):
    print(f"\n{label} (synthetic)")
    print(await PrettyScenarioResultPrinter(enable_colors=False).render_async(recorded_result))
    print(await JsonScenarioResultPrinter().render_async(recorded_result))

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

# %%
from azure.ai.contentsafety.models import TextCategory

from pyrit.scenario.airt import RapidResponse
from pyrit.score import (
    AzureContentFilterScorer,
    CompatibleScorers,
    FloatScaleThresholdScorer,
    ScorerCandidate,
    ScorerRequirements,
)

hate_scorer = FloatScaleThresholdScorer(
    scorer=AzureContentFilterScorer(harm_categories=[TextCategory.HATE]), threshold=0.5
)
violence_scorer = FloatScaleThresholdScorer(
    scorer=AzureContentFilterScorer(harm_categories=[TextCategory.VIOLENCE]), threshold=0.5
)
scorers = CompatibleScorers(
    candidates=[
        ScorerCandidate(scorer=hate_scorer, evaluation="harm.hate"),
        ScorerCandidate(scorer=violence_scorer, evaluation="harm.violence"),
    ]
)
harm_dataset_config = DatasetAttackConfiguration(
    sources=[
        DatasetSource(name="airt_hate", scorer_requirements=ScorerRequirements(evaluation="harm.hate")),
        DatasetSource(name="airt_violence", scorer_requirements=ScorerRequirements(evaluation="harm.violence")),
    ]
)

pool_scenario = RapidResponse(objective_scorer=scorers)
pool_scenario.set_params_from_args(args={"objective_target": objective_target, "dataset_config": harm_dataset_config})

# %% [markdown]
# Each dataset is judged by the candidate bound to the evaluation it names, and a dataset without
# `scorer_requirements` uses a candidate without one. If no candidate can judge a dataset's
# objectives, initialization fails and gives each candidate's reason. A technique that needs a
# particular kind of scorer, such as TAP, follows `incompatible_technique_policy`.

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
