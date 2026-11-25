# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.4
# ---

# %% [markdown]
# # Choosing a Threshold for `FloatScaleThresholdScorer`
#
# `FloatScaleThresholdScorer` turns a float score into true/false: a score at or above its
# `threshold` counts as true. The threshold decides how many real successes the scorer catches
# (recall) and how many failures it marks as successes (false positives), and a value picked by
# hand can be far from the best trade-off for a given scorer.
#
# `suggest_threshold` picks a threshold from scores that humans have labeled. It is opt-in and only
# suggests a value; nothing changes until you create a scorer with it. It:
#
# 1. splits the labeled responses into a tuning part and a holdout part, keeping the share of
#    successes the same in both,
# 2. tries every observed score on the tuning part and picks the one with the best F1 score, or,
#    with `min_precision`, the one with the best recall whose precision is at least `min_precision`,
# 3. applies that threshold, unchanged, to the holdout part. The holdout numbers are the ones to
#    trust: the tuning numbers look better than they will in practice, because the threshold was
#    picked to fit them.
#
# This example runs offline on made-up scores, so you can see the output without a model.

# %%
import numpy as np
import pandas as pd

from pyrit.score import suggest_threshold

rng = np.random.default_rng(0)
labels = rng.random(400) < 0.5  # True: humans say the objective was achieved
# A judge that is fairly good at ranking responses but rarely gives successes a very high score.
scores = np.clip(np.where(labels, rng.beta(4, 3, 400), rng.beta(1.2, 6, 400)), 0, 1).round(2)

suggestion = suggest_threshold(scores=scores, labels=labels, current_threshold=0.9)

rows = {
    f"current ({suggestion.current_threshold})": suggestion.current_holdout_point,
    f"suggested ({suggestion.threshold})": suggestion.holdout_point,
}
pd.DataFrame(
    {
        name: {
            "recall": point.recall,
            "precision": point.precision,
            "f1_score": point.f1_score,
            "false_positive": point.false_positive,
        }
        for name, point in rows.items()
    }
).T.round(3)

# %% [markdown]
# `operating_points` lists every candidate on the tuning split, which shows the whole
# recall/false-positive trade-off rather than one number.

# %%
pd.DataFrame([vars(point) for point in suggestion.operating_points])[
    ["threshold", "recall", "precision", "false_positive", "false_negative"]
].iloc[::8].round(3)

# %% [markdown]
# If false positives matter more than missed successes, set a precision floor instead of
# maximizing F1:

# %%
strict = suggest_threshold(scores=scores, labels=labels, min_precision=0.95)
print(
    f"threshold={strict.threshold}, holdout recall={strict.holdout_point.recall:.3f}, "
    f"holdout precision={strict.holdout_point.precision:.3f}"
)

# %% [markdown]
# ## With a real scorer
#
# `suggest_threshold_async` scores an OBJECTIVE `HumanLabeledDataset` with your
# `FloatScaleThresholdScorer` and keeps the float value behind each true/false verdict, so you do
# not need to collect the scores yourself. The scorer's own threshold is reported as
# `current_threshold`. With `num_scorer_trials` above 1, every trial is counted, so the numbers
# describe a single scoring run, which is how the scorer is used in an attack.
#
# ```python
# from pyrit.common.path import SCORER_EVALS_PATH
# from pyrit.prompt_target import OpenAIChatTarget
# from pyrit.score import (
#     FloatScaleThresholdScorer,
#     HumanLabeledDataset,
#     MetricsType,
#     SelfAskScaleScorer,
#     suggest_threshold_async,
# )
#
# judge = SelfAskScaleScorer(chat_target=OpenAIChatTarget())
# scorer = FloatScaleThresholdScorer(scorer=judge, threshold=0.9)
# dataset = HumanLabeledDataset.from_csv(
#     csv_path=SCORER_EVALS_PATH / "objective" / "cyber.csv", metrics_type=MetricsType.OBJECTIVE
# )
#
# suggestion = await suggest_threshold_async(scorer=scorer, labeled_dataset=dataset, num_scorer_trials=3)
# print(suggestion.threshold, suggestion.holdout_point)
#
# tuned_scorer = FloatScaleThresholdScorer(scorer=judge, threshold=suggestion.threshold)
# ```
#
# A threshold tuned on one dataset fits that dataset's mix of objectives and responses. Check the
# holdout numbers, and tune again when the judge model, rubric, or kind of objective changes.
