# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.17.3
# ---

# %% [markdown]
# # 1. Q&A Benchmark
#
# The `QuestionAnsweringBenchmark` can process Q&A datasets and evaluate how good a target is at answering the questions.

# %%
from pyrit.datasets.executors.question_answer.wmdp_dataset import fetch_wmdp_dataset
from pyrit.executor.attack import AttackScoringConfig, ConsoleAttackResultPrinter
from pyrit.executor.benchmark import QuestionAnsweringBenchmark
from pyrit.models import (
    QuestionAnsweringEntry,
    QuestionChoice,
)
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.score import SelfAskQuestionAnswerScorer
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

# Initialize PyRIT (load environment files and set central memory instance)
await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

# Define the scoring configuration for the benchmark
scoring_config = AttackScoringConfig(objective_scorer=SelfAskQuestionAnswerScorer(chat_target=OpenAIChatTarget()))

# Create the benchmark with scorer and execute with demo dataset
benchmark = QuestionAnsweringBenchmark(
    objective_target=OpenAIChatTarget(),
    attack_scoring_config=scoring_config,
)

question = QuestionAnsweringEntry(
    question="What is the capital of France?",
    answer_type="str",
    correct_answer=0,
    choices=[
        QuestionChoice(index=0, text="Paris"),
        QuestionChoice(index=1, text="London"),
        QuestionChoice(index=2, text="Berlin"),
        QuestionChoice(index=3, text="Madrid"),
    ],
)

result = await benchmark.execute_async(question_answering_entry=question)  # type: ignore
await ConsoleAttackResultPrinter().print_conversation_async(result)  # type: ignore

# %%
# Fetch WMDP dataset for Q/A Model Testing; this is big and can take a minute

wmdp_ds = fetch_wmdp_dataset(category="cyber")

# %%
# Evaluate the Q/A Model response
results = []
for question in wmdp_ds.questions[:3]:
    result = await benchmark.execute_async(question_answering_entry=question)  # type: ignore
    results.append(result)
    await ConsoleAttackResultPrinter().print_conversation_async(result=result)  # type: ignore

# %% [markdown]
# You can run custom analysis on the benchmarking results. See the below example on how to get the percentage of correct answers from the `AttackResult`.

# %%
from pyrit.models import AttackOutcome

success = sum(r.outcome == AttackOutcome.SUCCESS for r in results)
print(f"Success rate: {(success / len(results)) * 100:.2f}%")
