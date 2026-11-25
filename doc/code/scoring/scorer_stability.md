# Scorer stability across presentations

An attack success rate rests on a scorer answering true or false, one response at a time.
[Scorer metrics](./4_scorer_metrics.ipynb) measure how often those answers agree with human
labels. `ScorerStabilityEvaluator` measures something that needs no labels: whether the scorer
gives the same answer when the same question is put to it another way.

A presentation is one way of asking. The order the options are listed in, the wording of a
label, a paraphrase of the rubric, whether the objective comes before or after the response,
and formatting of the prompt all leave the question unchanged. A scorer that measures the
response should not move with them. When it does, the same attack on the same target reports a
different success rate, depending on a detail that is rarely written down.

Rewording a judge's rubric has been shown to flip its verdicts beyond what reruns alone do
[@weng2026policyinvariance], and so have formatting and paraphrase changes to what the judge is
shown [@dev2026judgereliability]. The evaluator measures such changes for any PyRIT true/false
scorer that can be configured for them.

## What moves the reported rate

For an attack success rate, read `positive_rate_shift` first:

- **`positive_rate_shift`**: how far the presentation moves the share of true verdicts from the
  reference's, on the responses both reached a verdict on. With an objective scorer this is how
  far the attack success rate moves.

A presentation can also change many verdicts without moving the reported rate, by moving as
many one way as the other. That is measured separately, below.

## Why a flip rate is not enough

Counting the responses whose verdict differs between two presentations overstates the effect,
because a scorer that samples also disagrees with itself when nothing changes. The evaluator
therefore scores every presentation more than once and splits what it sees:

```
flip rate = repeat flip rate + presentation effect
```

- **`flip_rate`**: share of run pairs, one under each presentation, whose verdicts differ.
- **`repeat_flip_rate`**: share of run pairs under the same presentation whose verdicts differ.
  This is the scorer's ordinary noise.
- **`presentation_effect`**: what is left. For one response, if the scorer answers true with
  chance `a` under the reference and `b` under the presentation, this is `(a - b) ** 2`,
  averaged over responses. It is zero for a scorer that ignores the presentation, however noisy
  that scorer is, and it equals the flip rate for a scorer that always repeats itself. It is a
  mean squared change: a presentation that moves every response's chance of a true verdict by
  0.2 reads as an effect of 0.04.

With a single run (`num_repeats=1`) the noise cannot be measured, so `repeat_flip_rate`,
`presentation_effect` and its p-value are `None`; the shift is still reported and tested.

## Running an evaluation

The scorer under test is the reference. Each `ScorerPresentation` names another way of asking
and carries a scorer already configured for it. Anything a scorer's constructor accepts can be
a presentation. For `SelfAskRefusalScorer` and `SelfAskGeneralTrueFalseScorer`,
`prompt_format_string` decides the field order, the field labels and the formatting of the
prompt:

```python
from pyrit.common.path import SCORER_EVALS_PATH
from pyrit.score import (
    HumanLabeledDataset,
    MetricsType,
    ScorerPresentation,
    ScorerStabilityEvaluator,
    SelfAskRefusalScorer,
)

evaluator = ScorerStabilityEvaluator(
    scorer=SelfAskRefusalScorer(chat_target=chat_target),
    presentations=[
        ScorerPresentation(
            name="response_first",
            scorer=SelfAskRefusalScorer(
                chat_target=chat_target,
                prompt_format_string="response_to_evaluate_input: {response}\nconversation_objective: {objective}",
            ),
        ),
        ScorerPresentation(
            name="short_labels",
            scorer=SelfAskRefusalScorer(
                chat_target=chat_target,
                prompt_format_string="objective: {objective}\nresponse: {response}",
            ),
        ),
        ScorerPresentation(
            name="trailing_newline",
            scorer=SelfAskRefusalScorer(
                chat_target=chat_target,
                prompt_format_string="conversation_objective: {objective}\nresponse_to_evaluate_input: {response}\n",
            ),
        ),
    ],
)

dataset = HumanLabeledDataset.from_csv(
    csv_path=SCORER_EVALS_PATH / "refusal_scorer" / "refusal.csv",
    metrics_type=MetricsType.OBJECTIVE,
)
metrics = await evaluator.evaluate_dataset_async(labeled_dataset=dataset, num_repeats=3)

for effect in metrics.effects:
    print(
        effect.name,
        f"rate moves {effect.positive_rate_shift:+.1%} (p={effect.positive_rate_shift_p_value:.3f})",
        f"flips {effect.flip_rate:.1%}",
        f"noise {effect.repeat_flip_rate:.1%}",
        f"effect {effect.presentation_effect:.3f} (p={effect.presentation_effect_p_value:.3f})",
        f"accuracy {effect.metrics.accuracy:.1%} vs {metrics.reference_metrics.accuracy:.1%}",
    )
```

For `SelfAskTrueFalseScorer`, a `system_prompt_template` that lists the false description before
the true one gives an option-order presentation, and a reworded `TrueFalseQuestion` gives a
paraphrase.

Every presentation must differ from the reference in configuration, and must use the same judge
model, temperature and top_p. The evaluator checks both when it is built, because a judge change
would read as a presentation effect. A run makes `(presentations + 1) x num_repeats x responses`
scoring calls. Each round scores every response once under every presentation, so a change in
the judge model over time falls on all of them alike.

On a labeled dataset, `reference_metrics` and each effect's `metrics` hold that scorer's
`ObjectiveScorerMetrics`, computed as `ObjectiveScorerEvaluator` computes them with `num_repeats`
trials. `accuracy`, `f1_score` and the rest therefore mean what they mean in the scorer metrics
registry. `num_repeats` defaults to 3, the number of trials `Scorer.evaluate_async` records.

Stability needs no labels. `evaluate_conversations_async` takes conversations and a
`ScoringExpectation` for each directly, so it also runs on the responses of an attack, and on
scorers that need typed conditions. The conversations must already be in memory, as an attack's
are, because each score refers to the stored response; nothing else is added to memory. With an
objective scorer, each `positive_rate_shift` is then how far the attack success rate of that run
would move under that presentation.

## Deciding whether an effect is real

Each presentation carries two permutation p-values, which make no assumption about the scorer:

- **`presentation_effect_p_value`** answers whether the presentation changes any response's
  chance of a true verdict. If it changes nothing, the runs of a response are interchangeable
  between the reference and the presentation, so they are reassigned at random to see how
  large an effect chance alone produces.
- **`positive_rate_shift_p_value`** answers whether the reported rate moves. Each response's
  shift is reversed at random. With one run per presentation this is the exact sign test on
  the responses whose verdict changed.

Both are Holm-adjusted across the presentations of one evaluation, each kind separately, so
comparing more presentations does not raise the chance of a false finding.

## What to check before trusting a result

- **The repeats must be independent runs.** A judge that is cached, or sampled with a pinned
  seed, repeats itself exactly. The repeat flip rate then reads zero and the judge's sampling
  noise is counted as a presentation effect, because a change of prompt redraws the sample.
- **A presentation must keep the meaning.** The evaluator cannot check this. A paraphrase that
  changes the criterion measures a different question, and its flips are not instability.
- **A run with no verdict removes the response from that comparison.** A response is left out
  instead of being counted as false. Each comparison uses the responses that reached a verdict
  in every run of both the reference and that presentation (`num_items`), and the reference's
  own figures use the responses it reached a verdict on in every run. One presentation's
  failures therefore change no other comparison's counts, rates or unadjusted p-values, nor the
  reference's figures. The Holm-adjusted p-values do depend on every presentation's p-value, so
  they can move. `num_undetermined_items` and
  `reference_num_undetermined_items` count the responses each lost, so a presentation that makes
  the judge fail more often is visible.

## Working from recorded verdicts

`metrics.to_json()` writes every recorded verdict with the metrics. `compute_scorer_stability`
computes the same stability metrics from recorded verdicts without calling a scorer, which also
covers verdicts collected by another tool:

```python
from pyrit.score import compute_scorer_stability

metrics = compute_scorer_stability(
    verdicts={
        "reference": [[True, True, False, False], [True, True, False, False]],
        "options_swapped": [[True, False, False, False], [True, False, False, True]],
    },
)
```

Each presentation maps to its runs, one row per run and one column per response, and every
presentation needs the same number of runs. Use `None` for a run that reached no verdict.
