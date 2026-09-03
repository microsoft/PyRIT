# Choosing Adversarial Models for Automated Red Teaming with PyRIT

3 Sep 2026 - Victor Valbuena, AIRT @ Microsoft

PyRIT helped us discover that we could improve the attack success rate of our adversarial chat target by more than 19 percentage points. This blog discusses methodology, so [jump to the findings](#what-we-observed) if you just want to see the benchmarks. The underlying benchmark data are available as CSV files along with this post.

On Microsoft's AI Red Team, we use PyRIT during our red teaming operations in which many automated attack techniques use adversarial models for attack orchestration. We want to make sure we're using the best adversarial model at our disposal for PyRIT, so we built a tool to automate the process of comparing how adversarial models perform. This became PyRIT's `AdversarialBenchmark` scenario, and on our team it has turned model selection from an intuition-driven choice into an evidence-based, repeatable evaluation. We intend to scale this evaluation through CI/CD to continuously discover the most effective models for automating red teaming, and since the scenario caches prior benchmarking results, we can assess model performance over generations and across families quickly and reliably. Interestingly, when we ran it against in-house models, we discovered that attack success rate (ASR) for models varies significantly across attack techniques. We'll explore that finding in this post. For readers who want to run the benchmark themselves, Grok 4.3 is the only model evaluated here that is publicly available and can be configured as an adversarial target in PyRIT. We present it as an accessible point of comparison, not as a general recommendation, as its performance varied by technique and scorer.

## Why the Adversarial Model Matters

Red teaming operations are increasingly orchestrated by agents built on advanced LLMs. The model orchestrating an attack often needs to:

- Plan the attack based on its given objective (or several objectives).
- Adapt to objective-target responses that may include denial, misdirection, or non-understanding.
- Operate through long conversation histories, including through multi-step exploits.
- Return reliably structured output for scoring and evaluation.

If the attacker model underperforms at any of these tasks, it becomes ineffective for the operation. Therefore, the best models for automated red teaming are usually both unaligned and adversarial. In this context, **alignment** means adherence to safety-focused post-training intended to make a model behave like a safe, helpful assistant. An **adversarial model** is a model (like an attacker model) used to generate or adapt attacks against another model. The choice of model therefore acts as a multiplier for successful automated red teaming. In this post, the victim model will often be referred to as the objective target, while the attacker model will be referred to as the adversarial model.

Models with fewer safety restrictions, including abliterated models, may be more willing to comply with requests to probe or bypass another model's defenses, but they may also be less consistent in achieving attack goals. Efficacy depends on several variables in a simulated operation: the type of objective, the attack technique being used, the objective target itself, and the scoring mechanism are a few of these. Thus the "best" model is not universally the "best", since a model may excel with one combination of these variables (e.g. attack technique or objective target) while underperforming with others.

## A Controlled Way to Compare Models

To address this ambiguity in determining adversarial model performance, PyRIT creates a controlled comparison by holding the objective target, objectives, scorer, and attack techniques constant while varying only the adversarial model, then measuring the resulting attack outcomes. This gives us a fair way to compare how models perform in narrow red teaming contexts.

### Designing a Fair Comparison

The `AdversarialBenchmark` scenario builds a matrix of attack techniques, adversarial models, and datasets. Users can evaluate model performance using a single technique, a focused named technique aggregate such as `light`, or a broader registered set of techniques. Each matrix entry corresponds to a tuple of dataset, technique, and model, such as `harmbench__red_teaming__qwen_mt`, and produces one result per selected objective. When `use_cached=True`, `AdversarialBenchmark` consults PyRIT's central memory to re-use existing records keyed on hashes for the same techniques and objective targets, as well as the names of the technique, adversarial target registry entry, and dataset name. Since the cache is greedy, one cached `success` or `failure` can skip the entry without checking that all objectives in the entry were attempted.

![Diagram showing AdversarialBenchmark holding dataset objectives, the objective target, the objective scorer, and attack techniques constant while candidate adversarial models vary to produce separate evaluation-plan entries and attack success rates by model.](2026_09_03_adversarial_model_selection_benchmark_design.png)

*Figure 1 - The benchmark varies the adversarial model while controlling the other evaluation inputs and expands the resulting dataset-technique-model tuples.*

Reproducibility and experimental fairness depend partially on the operator, since by default, the scenario chooses a subset of prompts from the dataset(s) provided at random, and variables like model temperature are target-specific. The fairest possible comparison pins specific prompts, adds strict and explicit failure tolerances (e.g. maximum retries), and uses a sufficiently large number of prompts from predetermined harm categories to measure usefulness in achieving a certain kind of objective.

### Measuring Attack Success

After the benchmark scenario executes, PyRIT retains the information needed to calculate the attack success rate (ASR) per adversarial model. Since a scorer produces a normalized `Score`, the attack interprets that score and stores an `AttackResult` whose outcome is `success`, `failure`, `error`, or `undetermined`. A `ScenarioResult` groups those records by atomic attack. A scenario can contain more than one `AttackResult` for the same objective and technique-model-dataset tuple; this can happen after a retry. To get ASRs, PyRIT ships with [a reporting script](https://github.com/microsoft/PyRIT/blob/main/build_scripts/export_adversarial_benchmark_result.py) that we use in our build pipeline to export benchmark results. For each combination, the script retains the newest result for each objective based on its timestamp and counts older records separately as `retry_records`. This prevents superseded records from counting as additional benchmark attempts, which would inflate the ASR denominator. From the retained results, the script then calculates the ASR:

$$
\mathrm{ASR}=\frac{\text{success}}{\text{success}+\text{failure}+\text{error}+\text{undetermined}}.
$$

The exporter output directory contains three views of the run, including the ASR grouped by attack technique and adversarial-model registry name, stored attack-result rows, and PyRIT's standard scenario summary.

## What We Observed

After creating the benchmark scenario, we ran it multiple times across several model families. The most useful finding was that the model powering an attack matters, but its advantage depends heavily on benchmark design and attack technique. We'll discuss how we discovered this chronologically.

We first ran the benchmark in the attacker-training pilot against Qwen-series models trained by our research team, described by Bullwinkel et al. in [Learning to Attack and Defend: Adaptive Red Teaming of Language Models via GRPO](https://arxiv.org/pdf/2606.09701). In this run, the attacker-trained single-turn Qwen variant (labeled Qwen ST) achieved 20 successes in 60 model-technique-objective combinations, an aggregate ASR of 33.3%. A legacy adversarial GPT-4o endpoint, acting as a baseline, achieved 3 of 60, or 5.0%, for a 28.3 percentage-point gap. An abliterated open-weight derivative that was not trained as an attacker reached 10.0%.

Next, we ran the benchmark to compare Grok 4.3 with a legacy GPT-4o adversarial endpoint on 50 pinned HarmBench objectives and on four attack techniques. Under that study's harm-proxy scorer, Grok achieved 124 successes in 200 model-technique-objective combinations, or 62.0%, compared with 85 of 200, or 42.5%, showing a 19.5-point gap.

Along with evidence that the legacy 4o endpoint was underperforming more recent models in orchestrating attacks, we realized we should compare our research team's models to Grok head-to-head. In these trials, we chose three models: Grok 4.3, the single-turn study variant ("Qwen ST"), and the multi-turn study variant ("Qwen MT"). We used 14 pinned objectives from HarmBench under four techniques against a GPT-4o objective target.

We refer to this as the head-to-head comparison: Qwen MT, Qwen ST, and Grok received the same 14 objectives under the same four techniques against the same objective target, with one scorer used consistently within each trial.

The first trial used a `FloatScaleThresholdScorer` (threshold 0.1) wrapping an `AzureContentFilterScorer`. Ultimately, 12 runs of the benchmark scenario completed and produced 168 final success-or-failure outcomes with complete responses and scores. We note that an audit of the database and logs found recovery activity that the final `AttackResult` retry fields did not expose. The Grok `role_play_video_game` benchmark combination retried one incomplete objective after an empty generated message caused a bad request. The Qwen ST `role_play_trivia_game` combination also retried one incomplete objective after Content Safety authentication failed. The Grok trivia combination also recovered internally from an empty HTTP 204 response. All 168 terminal outcomes form the primary benchmark result. As a sensitivity analysis, we also report `red_teaming` and `crescendo_simulated`, the two techniques with no recovery recorded across all three models. On that subset, Qwen ST achieved 19 of 28 (67.9%), Qwen MT achieved 17 of 28 (60.7%), and Grok achieved 14 of 28 (50.0%).

| Technique | Qwen MT | Qwen ST | Grok | Evidence status |
| --- | ---: | ---: | ---: | --- |
| `red_teaming` | 11/14 (78.6%) | 10/14 (71.4%) | 6/14 (42.9%) | No recovery recorded |
| `role_play_video_game` | 6/14 (42.9%) | 6/14 (42.9%) | 11/14 (78.6%) | Grok recovered through a scenario retry |
| `role_play_trivia_game` | 1/14 (7.1%) | 4/14 (28.6%) | 1/14 (7.1%) | Qwen ST retried the scenario; Grok retried an empty response internally |
| `crescendo_simulated` | 6/14 (42.9%) | 9/14 (64.3%) | 8/14 (57.1%) | No recovery recorded |
| **All terminal outcomes (primary)** | **24/56 (42.9%)** | **29/56 (51.8%)** | **26/56 (46.4%)** | Includes combinations with recorded recovery |
| **No-recovery sensitivity subset** | **17/28 (60.7%)** | **19/28 (67.9%)** | **14/28 (50.0%)** | `red_teaming` and `crescendo_simulated` |

### Variability Between Attack Techniques

The aggregate results conceal large intra-model differences. In the very first attacker-training pilot, the leading Qwen variant ranged from 60% ASR on `red_teaming` to 0% on `context_compliance`. In the separate Grok study, the observed gap ranged from 34 points on `red_teaming` to 4 points on `role_play_movie_script`.

![Two heatmaps of preliminary adversarial-model benchmark results. The attacker-training pilot compares four model aliases across red teaming, role play, and context compliance. The separate Grok study compares two model aliases across four techniques. A warning states that the panels use different victims, scorers, objective sets, and designs and should not be compared by absolute ASR.](2026_09_03_adversarial_model_selection_results.png)

*Figure 2 - Preliminary technique-level results from two separate, non-comparable studies.*

We could see that the best model for a simple conversational attack may not be the best model for a technique with different orchestration demands. More broadly, model selection also needs to account for capabilities such as long reasoning traces, reliable structured output, repeated backtracking, and multimodal support. A single ASR was therefore insufficient for choosing a "best" adversarial model. The head-to-head benchmarks showed the same interaction; in the no-recovery sensitivity subset, Qwen MT led on `red_teaming` at 78.6%, while Qwen ST led `crescendo_simulated` at 64.3%. Among the recovered benchmark combinations, Grok's final outcomes led `role_play_video_game` at 78.6%, and Qwen ST's final outcomes led `role_play_trivia_game` at 28.6%. No model led every technique.

![Grouped bars showing Qwen MT, Qwen ST, and Grok final attack success rates for four techniques, with combinations that had recorded recovery marked and a no-recovery sensitivity subset over red teaming and simulated crescendo.](2026_09_03_adversarial_model_selection_head_to_head.png)

*Figure 3 - Paired head-to-head across 14 objectives per technique-model pair. Hatched bars indicate recorded recovery; the no-recovery sensitivity subset uses only `red_teaming` and `crescendo_simulated`.*

The sanitized technique-level records behind the preliminary studies are available in [CSV form](2026_09_03_adversarial_model_selection_results.csv).

### Rerunning with a Task-Achievement LLM Judge

The head-to-head above scored success with a harm proxy: a `FloatScaleThresholdScorer` (threshold 0.1) wrapping `AzureContentFilterScorer`, which flags whether a response *looks* harmful across Hate, SelfHarm, Sexual, and Violence categories. Content scoring does not explicitly grade whether or not an objective was met, however. So to test how much the scorer choice matters in benchmarking, we re-ran the same three models across the same four techniques and the same 14 objectives against the same GPT-4o objective target, but replaced the objective scorer with a task-achievement LLM judge (a `SelfAskTrueFalseScorer` using the refined task-achieved rubric, evaluated by a separate judge model). This run executed as a single combined benchmark scenario rather than twelve isolated ones. We observed significant variance in ASR depending on the attack technique.

| Technique | Qwen MT | Qwen ST | Grok | Evidence status |
| --- | ---: | ---: | ---: | --- |
| `red_teaming` | 10/14 (71.4%) | 12/14 (85.7%) | 10/14 (71.4%) | Grok recovered through target-level retries after two consecutive HTTP-204 empty responses |
| `role_play_video_game` | 3/14 (21.4%) | 7/14 (50.0%) | 5/14 (35.7%) | No recovery recorded |
| `role_play_trivia_game` | 1/14 (7.1%) | 2/14 (14.3%) | 0/14 (0.0%) | No recovery recorded |
| `crescendo_simulated` | 6/14 (42.9%) | 6/14 (42.9%) | 1/14 (7.1%) | No recovery recorded |
| **All-technique aggregate** | **20/56 (35.7%)** | **27/56 (48.2%)** | **16/56 (28.6%)** | One combined run |

![Screenshot of PyRIT's scenario-results view for the combined adversarial benchmark, showing its scorer, overall statistics, and per-model breakdown.](2026_09_03_adversarial_model_selection_benchmark_screenshot.png)

*Figure 4 - PyRIT's scenario-results view for the combined task-achievement run, showing 168 attack results across 14 objectives and grouped success rates for the three adversarial models.*

The per-group names in the screenshot are the target-registry aliases used for this run: `adversarial_chat_multiturn` is Qwen MT, `adversarial_chat_singleturn` is Qwen ST, and `adversarial_chat` is Grok.

`Total Techniques: 3` in the screenshot reflects how the current printer counts the three adversarial-model display groups; the underlying run included the four techniques listed above.

A notable example from the transcripts is a HarmBench instance requesting SQL injection instructions. This is a classic cybersecurity exploit that can provide uplift to attackers and autonomous agents. In this case, we observed that Grok successfully induced the target model to produce the injection using a role-playing technique, but failed to do the same while using Crescendo. The opposite was observed for the attacker-trained Qwen single-turn model.

![A two-by-two comparison of final attacker prompts and objective-target response excerpts for Qwen Singleturn and Grok using simulated Crescendo and video-game role play. Qwen succeeds with Crescendo and fails with role play, while Grok shows the opposite outcomes.](2026_09_03_adversarial_model_selection_prompt_contrast.png)

*Figure 5 - Example transcript showing how success on an SQL injection (SQLi) objective varied by attack technique.*

We observed the same reversal for rootkit-code obfuscation: Qwen MT succeeded with `red_teaming`, while Grok succeeded with `crescendo_simulated`.

![A two-by-two comparison of final attacker prompts and objective-target response excerpts for Qwen Multiturn and Grok using red teaming and simulated Crescendo. Qwen succeeds with red teaming and fails with Crescendo, while Grok shows the opposite outcomes.](2026_09_03_adversarial_model_selection_rootkit_prompt_contrast.png)

*Figure 6 - Example transcript for rootkit-code obfuscation success varying by attack technique.*

Importantly, the scorer was the major intentional design change, and the task-achievement rerun produced a different model ordering. Because that rerun also used a combined harness and generated a new set of model responses, the comparison is directional rather than an isolated estimate of scorer effect. Under the harm proxy's scoring mechanism, Grok ranked second (46.4%); in the task-achievement run it ranked last (28.6%), while Qwen ST led in both runs. Results also moved in different directions across individual technique-model combinations: Grok's `red_teaming` ASR was higher (42.9% to 71.4%), while its `role_play_video_game` (78.6% to 35.7%) and `crescendo_simulated` (57.1% to 7.1%) ASRs were lower. We theorize the harm-proxy content classifier rewarded harmful-sounding but off-objective text. This does not mean that using a content classifier is right or wrong; rather, it shows that the definition of "most adversarial behavior" depends on the scoring mechanism.

![Grouped bars showing Qwen MT, Qwen ST, and Grok task-achievement success rates for four techniques and an all-technique aggregate, with the recovered Grok red-teaming combination hatched.](2026_09_03_adversarial_model_selection_llm_judge.png)

*Figure 7 - The same model-technique-objective comparison rerun with a task-achievement LLM judge in one combined benchmark scenario. The hatched Grok `red_teaming` combination recovered from two consecutive HTTP-204 empty responses through target-level retries.*

Per-combination counts for both runs are logged as [ACS run CSV](2026_09_03_adversarial_model_selection_acs_run.csv) and [LLM-judge run CSV](2026_09_03_adversarial_model_selection_llm_judge_run.csv).

## Limitations and Next Steps

The adversarial benchmark is available for you to try as the `AdversarialBenchmark` class. See [Benchmark Scenarios](../scanner/benchmark.ipynb) for its behavior, configuration, and examples.

As we improve the benchmark, this blog post may become out of date, but the general theme will stay the same: PyRIT can help evaluate which models perform best as attackers. The findings are preliminary, have limitations, and are not a substitute for repeated, controlled measurement:

- The attacker-training pilot covered three relatively simple techniques, and its two datasets were not repeated identical trials.
- The separate Grok comparison used one victim, one run, four techniques, and a noisy harm-proxy scorer. Its 50-objective sample also overrepresented copyright extraction, while several harm-category slices were too small to interpret independently.
- The two head-to-head studies were run differently. The first harm-proxy head-to-head (Figure 3) executed as twelve isolated single-combination benchmark scenarios. Two combinations required a scenario-level retry and one recovered internally from an HTTP-204 empty response. The task-achievement rerun (Figures 4-7) was one combined benchmark run in which a single Grok `red_teaming` attack recovered from two consecutive HTTP-204 empty responses through target-level retries. Both runs produced 168 terminal success-or-failure outcomes with no error or undetermined results. Because the two studies differ in scorer, harness (isolated versus combined), and run instance at once, their differences should be read as motivating the scorer question rather than as an isolated measurement of scorer effect. The available artifacts also record registry aliases rather than exact adversarial deployment versions. Per-run, per-combination statistics are provided as CSVs alongside this post.

As a red teaming tool, PyRIT's strength depends on high-quality adversarial models, so we will keep building tooling to identify and integrate them. Our north star is to continuously evaluate new adversarial models as they become available and publish evidence about how they perform across automated red teaming tasks. To that end, we intend to gather data across more models, objective targets, techniques, modalities, and balanced objective sets. Repeated paired trials, scorer calibration against human labels, held-out evaluation, confidence intervals, and explicit latency, reliability, and cost metrics would make model selection more defensible.

We also intend to integrate the benchmark into CI/CD so we can continuously evaluate effective adversarial models and help automated operations keep pace with frontier-model improvements. We hope to publish benchmark results continuously by using PyRIT's benchmark scenario, and possibly add other benchmark scenarios to focus on variables other than the adversarial model, like the scorer(s) and objective target(s).

Until then, when using the adversarial benchmark scenario, do not treat the aggregated attack success rates it returns as broad evidence of red-team agent performance. Pooled ASRs hide nuances of per-technique success, dataset objectives, and scoring mechanisms. Use the benchmark scenario as an indicator for future investigation.

---
