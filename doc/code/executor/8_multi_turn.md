# Multi-Turn Attacks

A multi-turn attack drives a **conversation**. An adversarial chat model generates each next prompt
based on how the target responded, and a [scorer](../scoring/0_scoring.ipynb) decides when the
objective is met. The attack keeps iterating until it succeeds or hits a turn limit. Because they
adapt to the target and exploit conversation history, multi-turn attacks tend to elicit harm more
reliably than single-turn ones — at the cost of an extra (adversarial) model and more requests.

| Attack | What it does |
|---|---|
| [Red Teaming](9_red_teaming.ipynb) | The general multi-turn attack: an adversarial model probes the target turn by turn toward the objective. |
| [Crescendo](10_crescendo.ipynb) | Starts benign and escalates gradually across turns, each step building on the last. |
| [Tree of Attacks with Pruning (TAP)](11_tap.ipynb) | Searches a tree of adversarial prompts, pruning unpromising branches. Works even on targets that take single prompts. |
| [Multi-Prompt Sending](12_multi_prompt_sending.ipynb) | Sends a predetermined sequence of prompts within one conversation. |
| [Chunked Request](13_chunked_request.ipynb) | Splits a harmful request across several turns so no single message looks unsafe. |

## Compound and streaming attacks

Two specialized families build on the multi-turn idea:

- **Compound** — [Sequential Attack](14_sequential.ipynb) runs other attacks in order under a
  completion policy (e.g. *try Crescendo first, fall back to Prompt Sending*). Each inner attack
  keeps its own `AttackResult`; the envelope exposes them as children, preserving the
  one-objective → one-result invariant.
- **Streaming** — [Barge-In Attack](15_barge_in.ipynb) interrupts a target that streams its response,
  injecting follow-up input mid-generation.

See [Attack Configuration](16_attack_configuration.ipynb) for the adversarial, scoring, and converter
configurations these attacks accept.
