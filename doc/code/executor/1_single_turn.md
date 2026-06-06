# Single-Turn Attacks

A single-turn attack tries to achieve its objective in **one new prompt**. It may prepare the
conversation first — prepending a benign exchange, many-shot examples, or a role-play frame — but
the actual ask lands in a single message, and an optional [scorer](../scoring/0_scoring.ipynb)
decides whether it worked. Single-turn attacks need no adversarial chat model, which makes them fast
and cheap to run.

> **Single-turn attacks are often just [attack techniques](../scenarios/0_attack_techniques.ipynb).**
> Most of these are a `PromptSendingAttack` plus a specific set of seeds or a fixed configuration.
> If you are tempted to write a new single-turn subclass, first check whether a registered technique
> already expresses it — scenarios can then select it by name.

| Attack | What it does |
|---|---|
| [Prompt Sending](2_prompt_sending.ipynb) | Sends objectives straight to the target, optionally with converters and a scorer. The base building block. |
| [Role Play](3_role_play.ipynb) | Wraps the objective in a fictional frame (e.g. a movie script) so the target is more likely to comply. |
| [Context Compliance](4_context_compliance.ipynb) | Seeds the conversation with a benign Q&A so an injected assistant turn makes the harmful ask look already-agreed. |
| [Many-Shot Jailbreak](5_many_shot.ipynb) | Prepends many faux question/answer pairs that demonstrate compliance, then asks the real question. |
| [Skeleton Key](6_skeleton_key.ipynb) | Issues a known jailbreak that asks the model to revise its own safety guidelines. |
| [Flip](7_flip.ipynb) | Obfuscates the prompt (e.g. reversing characters) and asks the model to decode and answer. |

Each notebook is a short, runnable example. They all follow the same shape from the
[Executor overview](0_executor.md): construct the attack, call `execute_async(objective=...)`, and
read the `AttackResult`. See [Attack Configuration](16_attack_configuration.ipynb) for the inputs
(prepended conversations, multimodal seeds, labels) that every attack accepts.
