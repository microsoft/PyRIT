# Executor

An **executor** is an *algorithm for interacting with an objective target*. You give it an objective
and some configuration, it drives the target, and it hands back a result. That's the whole job.

The important thing to notice up front is that **not every executor is an attack**. Sending a single
adversarial prompt is an executor, but so is running a Q&A benchmark over a dataset, fuzzing to
generate new prompts, or orchestrating a cross-domain injection workflow. Attacks are the largest and
most familiar family, but every category in this section — attacks, workflows, benchmarks, and prompt
generators — is the same kind of object running the same lifecycle.

## Objective target vs. adversarial target

Two targets show up constantly in this section, and keeping them straight is the single most useful
piece of vocabulary here:

- The **objective target** is the system *under test* — the model or endpoint you are trying to elicit
  a behavior from. For attacks and benchmarks it is the `objective_target=` argument. (A few executor
  families use targets in other roles — workflows distinguish a setup target from a processing target,
  and some prompt generators use the passed model to *generate* rather than to test — and those pages
  call out the difference.)
- The **adversarial target** (often an *adversarial chat*) is a model that **PyRIT controls** to
  *generate* attack prompts on your behalf. **Only some executors use one**: adaptive multi-turn
  attacks need it to drive the conversation, and a handful of single-turn attacks use it to craft the
  prompt. It works best as an unfiltered model so it doesn't refuse to produce adversarial content. In
  code it is passed via `AttackAdversarialConfig(target=...)`.

So whenever you see `objective_target=` you are wiring up the system under test; whenever you see an
adversarial config you are wiring up the attacker model. They are distinct roles — usually separate
deployments, though nothing stops you pointing both at the same model if you mean to.

## Executor vs. attack technique

These two words get used loosely, so we pin them down:

- An **executor** (for attacks, an **attack strategy**) is the *algorithm* — e.g.
  `PromptSendingAttack`, `CrescendoAttack`, `TreeOfAttacksWithPruningAttack`. It knows *how* to drive
  the objective target.
- An **[attack technique](../scenarios/0_attack_techniques.ipynb)** is anything that, once configured,
  generally helps move an attack toward achieving its objective — a role-play framing, a many-shot
  priming set, a particular jailbreak template. A technique is **specific to an attack**: it is one
  configured executor (plus its seeds) packaged so a [scenario](../scenarios/0_scenarios.ipynb) can
  select it by name. The technique is the *recipe*; the executor is the *engine* that runs it.

You use executors directly when you want fine-grained control (the rest of this section). You reach
for techniques when you want a scenario to select and run many attacks for you. Notably, **many
single-turn attacks are really just techniques** — a `PromptSendingAttack` plus a particular set of
seeds — so before writing a new single-turn subclass, check whether a registered technique already
does the job.

## Single-turn vs. multi-turn

The cleanest way to tell the two main attack families apart is to **count requests to the objective
target**:

- A **single-turn** attack sends a single prompt — **one attack turn** — to the objective target. It
  may prepare that prompt elaborately (a role-play frame, many-shot priming, a prepended conversation),
  but only one crafted message is the actual ask, so no adversarial target is required to *drive* it.
- A **multi-turn** attack sends **more than one** turn, adapting as it goes. Many multi-turn attacks
  use an adversarial target to generate each next prompt from the objective target's responses; others
  send a fixed sequence of prompts, request the answer in chunks, or stream input — no adversarial
  target needed.

A third family, **compound attacks**, doesn't add turns of its own — it orchestrates *other* attacks
(running them in sequence) toward a single objective. It's covered last, after the building blocks it
composes.

## When do you actually need an attack?

The durable value of an attack is **adaptive decision-making**: branching and backtracking based on
the objective target's feedback, like searching a graph for a path that works. Crescendo and TAP are
the clearest examples — and you can reshape them substantially just by swapping their *primitives*
(system prompt, converters, scorers, prepended/simulated conversations) rather than writing a new
class.

A lot of what *looks* like a distinct attack isn't an adaptive algorithm at all:

- **Pure prompt transformations** — obfuscating, or deconstructing-and-reconstructing a prompt — are
  better expressed as [converters](../converters/0_converters.ipynb) than as attack classes.
- **Fixed framings** — a role-play wrapper, a primed Q&A history — are really a prepended conversation
  plus seeds, i.e. an [attack technique](../scenarios/0_attack_techniques.ipynb) over
  `PromptSendingAttack`.

Several of the single-turn attacks in this section predate that distinction and remain as classes for
compatibility. When you are building something new, prefer a converter or a technique — reach for a
new attack class only when you genuinely need a feedback-driven loop.

## The shared lifecycle

Every executor is a `Strategy`, and `Strategy.execute_async()` is non-abstract: it enforces the same
four phases for every executor, whether it sends one prompt or runs a whole benchmark.

- `_validate_context` — check the inputs
- `_setup_async` — initialize state
- `_perform_async` — run the core algorithm
- `_teardown_async` — clean up, always (in a `finally`)

This guarantees setup runs before the work, the work runs only if setup succeeds, teardown runs even
when setup or the core work errors, and logging/error handling is centralized. Every category below
inherits this contract.

## The shape of an attack

Attacks — the most common executors — share a 4-component shape:

```{mermaid}
flowchart LR
    A(["Attack Strategy"])
    A --consumes--> B(["Attack Context <br>(objective, labels, prepended conversation)"])
    A --configured by--> D(["Attack Configurations <br>(Adversarial, Scoring, Converter)"])
    A --produces--> C(["Attack Result"])
```

To run one:

1. Initialize a **strategy** with optional **configurations** (converters, scorers, adversarial target).
2. Call `execute_async(...)` with an **objective** (and optional prepended conversation / next message).
3. Receive an **`AttackResult`** describing what happened and whether the objective was met.

The context is created for you from the `execute_async` arguments — you rarely build one by hand.
See [Attack Configuration](3_attack_configuration.ipynb) for what you can put in the context and
configs (prepended conversations, multimodal seeds, next-turn messages, memory labels).

## Categories

- **[Single-Turn](1_single_turn.ipynb)** — send one prompt to the objective target and score the
  response. No adversarial target required.
- **[Multi-Turn](2_multi_turn.ipynb)** — send several turns to the objective target until the objective
  is met or a turn limit is hit; adaptive variants use an adversarial target to drive each next prompt.
- **[Attack Configuration](3_attack_configuration.ipynb)** — the cross-cutting inputs (prepended
  conversations, multimodal seeds, next-turn messages, labels) that every attack accepts.
- **[Workflow](4_workflow.ipynb)** — generic multi-step orchestration that doesn't fit the
  attack/benchmark mould (e.g. cross-domain prompt injection / XPIA).
- **[Benchmark](5_benchmark.ipynb)** — evaluate an objective target against a fixed dataset and
  criteria (e.g. Q&A accuracy, bias).
- **[Prompt Generator](6_promptgen.ipynb)** — produce attack prompts (e.g. fuzzing, Anecdoctor) to
  augment datasets; some generate from a model alone, others probe a target to evolve effective prompts.
- **[Compound](7_compound.ipynb)** — attacks that orchestrate *other* attacks toward one objective
  (e.g. running Crescendo, then falling back to Prompt Sending).

The following pages walk through each, with short runnable examples.
