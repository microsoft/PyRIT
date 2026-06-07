# Executor

The **executor** is the engine room of PyRIT: it takes prompts, sends them to a target, and
decides what happens next. Every executor — whether it sends a single prompt, runs a multi-turn
conversation, orchestrates a workflow, or scores a benchmark — shares one lifecycle and one
4-component shape. This page covers that shared shape; the sections below it cover each category.

## Executor vs. attack technique

Two words get used loosely, so we pin them down here:

- An **executor** (here, an **attack strategy**) is the *algorithm* — e.g. `PromptSendingAttack`,
  `CrescendoAttack`, `TreeOfAttacksWithPruningAttack`. It knows *how* to drive a target.
- An **[attack technique](../scenarios/0_attack_techniques.ipynb)** is one configured executor
  packaged with its seeds so a [scenario](../scenarios/0_scenarios.ipynb) can pick it by name. It is
  the *recipe*, not the engine.

You use executors directly when you want fine-grained control (the rest of this section). You reach
for techniques when you want a scenario to select and run many attacks for you. Notably, **many
single-turn attacks are really just techniques** — a `PromptSendingAttack` plus a particular set of
seeds — so before writing a new single-turn subclass, check whether a registered technique already
does the job.

## The four components

Every executor is an `AttackStrategy` (a `Strategy`) and follows the same flow:

```{mermaid}
flowchart LR
    A(["Attack Strategy"])
    A --consumes--> B(["Attack Context <br>(objective, labels, prepended conversation)"])
    A --configured by--> D(["Attack Configurations <br>(Adversarial, Scoring, Converter)"])
    A --produces--> C(["Attack Result"])
```

To run one:

1. Initialize a **strategy** with optional **configurations** (converters, scorers, adversarial chat).
2. Call `execute_async(...)` with an **objective** (and optional prepended conversation / next message).
3. Receive an **`AttackResult`** describing what happened and whether the objective was met.

The context is created for you from the `execute_async` arguments — you rarely build one by hand.
See [Attack Configuration](3_attack_configuration.ipynb) for what you can put in the context and
configs (prepended conversations, multimodal seeds, next-turn messages, memory labels).

## The shared lifecycle

`Strategy.execute_async()` is non-abstract and enforces the same four phases for every executor:

- `_validate_context` — check the inputs
- `_setup_async` — initialize state
- `_perform_async` — run the core algorithm
- `_teardown_async` — clean up, always (in a `finally`)

This guarantees setup runs before the attack, the attack runs only if setup succeeds, teardown
always runs even on error, and logging/error handling is centralized. Every category below inherits
this contract.

## Categories

- **Single-Turn** — send a prompt (or a prepared conversation ending in one new prompt) and score
  the response. No adversarial model required.
- **Multi-Turn** — an adversarial model iterates against the target until the objective is met or a
  turn limit is hit. Includes compound attacks (running other attacks) and streaming attacks.
- **Attack Configuration** — the cross-cutting inputs (prepended conversations, multimodal seeds,
  next-turn messages, labels) that every attack accepts.
- **Workflow** — generic multi-step orchestration that doesn't fit the attack/benchmark mould
  (e.g. cross-prompt injection / XPIA).
- **Benchmark** — evaluate a model against a fixed dataset and criteria (e.g. Q&A accuracy).
- **Prompt Generator** — produce adversarial prompts (e.g. fuzzing, Anecdoctor) to augment datasets.

The following pages walk through each, with short runnable examples.
