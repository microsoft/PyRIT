# Scorer stability across presentations

An attack success rate rests on a scorer answering true or false, one response at a time.
[Scorer metrics](./4_scorer_metrics.ipynb) measure how often those answers agree with human
labels. `ScorerStabilityEvaluator` measures something that needs no labels: whether the scorer
gives the same answer when the same question is put to it another way.

A presentation is one way of asking. The order the options are listed in, the wording of a
label, a paraphrase of the rubric, whether the objective comes before or after the response,
and formatting around the response all leave the question unchanged. A scorer that measures
the response should not move with them. When it does, the same attack on the same target
reports a different success rate, depending on a detail that is rarely written down.

Rewording a judge's rubric has been shown to flip its verdicts beyond what reruns alone do
[@weng2026policyinvariance], and so have formatting and paraphrase changes to the response it
is shown [@dev2026judgereliability]. The evaluator measures both kinds of change for any
PyRIT true/false scorer.

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
  that scorer is, and it equals the flip rate for a scorer that always repeats itself.

A presentation can also change many verdicts without moving the reported rate, by moving as
many one way as the other. That is reported separately:

- **`positive_rate_shift`**: the presentation's share of true verdicts minus the reference's.
  It equals `flip_to_true_rate - flip_to_false_rate`.
- **`positive_rate_range`**: the lowest and highest share of true verdicts over the reference
  and every presentation, which is the range of rates the same scorer reports on the same
  responses.

## Running an evaluation

The scorer under test is the reference. Each `ScorerPresentation` names another way of asking
and carries a scorer configured for it. Anything a scorer's constructor accepts can be a
presentation. For `SelfAskRefusalScorer`, the user prompt format decides the field order and
the field labels:

```python
from pyrit.common.path import SCORER_EVALS_PATH
from pyrit.converter import SuffixAppendConverter
from pyrit.score import (
    HumanLabeledDataset,
    MetricsType,
    PresentationKind,
    ScorerPresentation,
    ScorerStabilityEvaluator,
    SelfAskRefusalScorer,
)

scorer = SelfAskRefusalScorer(chat_target=chat_target)
evaluator = ScorerStabilityEvaluator(
    scorer=scorer,
    presentations=[
        ScorerPresentation(
            name="response_first",
            kind=PresentationKind.FIELD_ORDER,
            scorer=SelfAskRefusalScorer(
                chat_target=chat_target,
                prompt_format_string="response_to_evaluate_input: {response}\nconversation_objective: {objective}",
            ),
        ),
        ScorerPresentation(
            name="short_labels",
            kind=PresentationKind.LABEL_WORDING,
            scorer=SelfAskRefusalScorer(
                chat_target=chat_target,
                prompt_format_string="objective: {objective}\nresponse: {response}",
            ),
        ),
        ScorerPresentation(
            name="trailing_newline",
            kind=PresentationKind.FORMATTING,
            scorer=scorer,
            response_converters=[SuffixAppendConverter(suffix="\n")],
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
        f"flips {effect.flip_rate:.1%}",
        f"noise {effect.repeat_flip_rate:.1%}",
        f"effect {effect.presentation_effect:.1%} (p={effect.presentation_effect_p_value:.3f})",
        f"rate moves {effect.positive_rate_shift:+.1%}",
    )
print("reported rate ranges over", metrics.positive_rate_range)
```

`response_converters` change how the response is shown to the scorer, so they work with any
scorer, including ones that call no model. The stored original value is left as it was. For
`SelfAskTrueFalseScorer`, a `system_prompt_template` that lists the false description before
the true one gives an option-order presentation, and a reworded `TrueFalseQuestion` gives a
paraphrase.

Stability needs no labels. `evaluate_conversations_async` takes conversations and a
`ScoringExpectation` for each directly, so it also runs on the responses of an attack, and on
scorers that need typed conditions. With an objective scorer,
`positive_rate_range` is then the range of attack success rates that scorer would have
reported for that run. When gold labels are present, each presentation also gets its
`accuracy` and `accuracy_shift`, and the metrics report accuracy separately for the responses
whose verdict held in every run (`accuracy_on_consistent_items`) and for the rest
(`accuracy_on_inconsistent_items`).

## Deciding whether an effect is real

Each presentation carries two permutation p-values, which make no assumption about the scorer:

- **`presentation_effect_p_value`** answers whether the presentation changes any response's
  chance of a true verdict. If it changes nothing, the runs of a response are interchangeable
  between the reference and the presentation, so they are reassigned at random to see how
  large an effect chance alone produces.
- **`positive_rate_shift_p_value`** answers whether the reported rate moves. Each response's
  shift is reversed at random. With one run per presentation this is the exact sign test on
  the responses whose verdict changed. It is `None` when recorded verdicts give the reference
  and the presentation different numbers of runs, where reversing shifts is not a fair
  comparison.

The intervals (`presentation_effect_interval`, `positive_rate_shift_interval`,
`accuracy_shift_interval`) are Student t intervals over responses and describe the size of an
effect. They can be too narrow when few responses ever change verdict, so use the p-value to
decide whether an effect is there. P-values are for one presentation at a time and are not
adjusted for the number of presentations compared.

## What to check before trusting a result

- **The repeats must be independent runs.** A judge that is cached, or sampled with a pinned
  seed, repeats itself exactly. The repeat flip rate then reads zero and the judge's sampling
  noise is counted as a presentation effect, because a change of prompt redraws the sample.
- **A presentation must keep the meaning.** The evaluator cannot check this. A paraphrase that
  changes the criterion measures a different question, and its flips are not instability.
- **A run with no verdict removes the response.** A response that any run of any presentation
  left undetermined is left out of every rate instead of being counted as false, and
  `num_undetermined_items` reports how many each presentation lost.
- **`consistent_rate` and the ranges depend on how much was run.** More presentations and more
  repeats can only lower the one and widen the others, so compare them between evaluations
  with the same presentations and repeats.

## Working from recorded verdicts

`metrics.to_json()` writes every recorded verdict with the metrics. `compute_scorer_stability`
computes the same metrics from recorded verdicts without calling a scorer, which also covers
verdicts collected by another tool:

```python
from pyrit.score import compute_scorer_stability

metrics = compute_scorer_stability(
    verdicts={
        "reference": [[True, True, False, False], [True, True, False, False]],
        "options_swapped": [[True, False, False, False], [True, False, False, True]],
    },
    gold_labels=[True, True, False, False],
)
```

Each presentation maps to its runs, one row per run and one column per response. Use `None` for
a run that reached no verdict.
