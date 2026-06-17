# Executor

An **executor** is an *algorithm for interacting with an objective target*. You give it an objective
and some configuration, it drives the target, and it hands back a result. That's the whole job.

The important thing to notice up front is that **not every executor is an attack**. Sending a single
adversarial prompt is an executor, but so is running a Q&A benchmark over a dataset, fuzzing to
generate new prompts, or orchestrating a cross-domain injection workflow. Attacks are the largest and
most familiar family, but every category in this section — attacks, workflows, benchmarks, and prompt
generators — is the same kind of object running the same lifecycle.

## Executor vs. attack

An **executor** is the *algorithm* — for attacks, the **attack strategy** (e.g. `PromptSendingAttack`,
`CrescendoAttack`, `TreeOfAttacksWithPruningAttack`). It knows *how* to drive the objective target. An
**attack** is just the most common kind of executor.

Don't confuse the executor with an **[attack technique](../scenarios/0_attack_techniques.ipynb)** — a
configured recipe (a role-play framing, a many-shot priming set) that a
[scenario](../scenarios/0_scenarios.ipynb) selects by name. The technique is the *recipe*; the executor
is the *engine* that runs it. Techniques are defined fully in the scenarios docs.

## Executor categories

PyRIT ships several families of executor — attacks are the largest, alongside workflows, benchmarks,
and prompt generators. Attacks themselves split by a simple rule: **count requests to the objective
target** — a single-turn attack sends exactly one; a multi-turn attack sends more than one and adapts
as it goes.

- **[Single-Turn](1_single_turn.ipynb)** — sends a single prompt (**one attack turn**) to the
  objective target and scores the response. It may prepare that prompt elaborately (a role-play frame,
  many-shot priming, a prepended conversation), but only one crafted message is the actual ask, so no
  adversarial target is required to *drive* it.
- **[Multi-Turn](2_multi_turn.ipynb)** — sends **more than one** turn to the objective target,
  adapting until the objective is met or a turn limit is hit. Adaptive variants use an adversarial
  target to generate each next prompt from the responses; others send a fixed sequence, request the
  answer in chunks, or stream input — no adversarial target needed.
- **[Compound](4_compound.ipynb)** — doesn't add turns of its own; it orchestrates *other* attacks
  (running them in sequence) toward a single objective, after the building blocks it composes.
- **[Workflow](5_workflow.ipynb)** — generic multi-step orchestration that doesn't fit the
  attack/benchmark mould (e.g. cross-domain prompt injection / XPIA).
- **[Benchmark](6_benchmark.ipynb)** — evaluates an objective target against a fixed dataset and
  criteria (e.g. Q&A accuracy, bias).
- **[Prompt Generator](7_promptgen.ipynb)** — produces attack prompts (e.g. fuzzing, Anecdoctor) to
  augment datasets; some generate from a model alone, others probe a target to evolve effective
  prompts.

**[Attack Configuration](3_attack_configuration.ipynb)** isn't an executor — it's the cross-cutting
inputs every attack accepts (objective vs. adversarial target, prepended conversations, multimodal
seeds, next-turn messages, memory labels). It has its own page.

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

The category pages above each walk through their executors with short runnable examples.

## When do you actually need a new executor class?

Most of an executor's behavior comes from its *configuration and data*, not from new code. So before
writing a new executor class, ask whether the algorithm is genuinely new — or whether an existing
executor with different primitives would do.

For attacks specifically, the durable value of a new class is **adaptive decision-making**: branching
and backtracking based on the objective target's feedback, like searching a graph for a path that
works. Crescendo and TAP are the clearest examples — and you can reshape them substantially just by
swapping their *primitives* (system prompt, converters, scorers, prepended/simulated conversations)
rather than writing a new class.

A lot of what *looks* like a distinct executor isn't a new algorithm at all:

- **Pure prompt transformations** — obfuscating, or deconstructing-and-reconstructing a prompt — are
  better expressed as [converters](../converters/0_converters.ipynb) than as attack classes.
- **Fixed framings** — a role-play wrapper, a primed Q&A history — are really a prepended conversation
  plus seeds, i.e. an [attack technique](../scenarios/0_attack_techniques.ipynb) over an existing
  attack like `PromptSendingAttack`.
- **New datasets or criteria** — a different benchmark question set or a different scorer is data and
  configuration for an existing executor, not a new class.

Several of the single-turn attacks in this section predate this guidance and remain as classes for
compatibility. When you are building something new, prefer configuration, a converter, or a technique —
reach for a new executor class only when you genuinely need a new algorithm (most often a
feedback-driven loop).
