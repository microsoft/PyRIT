## GCG Experiments

This directory contains the public entry point for running the [Greedy Coordinate
Gradient (GCG) attack](https://arxiv.org/abs/2307.15043).

### Public API

The primary entry point is `GCG.execute_async` (`GCG` is an alias for
`GCGGenerator`):

```python
import asyncio

from pyrit.executor.promptgen.gcg import GCG, GCGModelConfig

generator = GCG(
    models=[GCGModelConfig(name="meta-llama/Llama-2-7b-chat-hf")],
)
result = asyncio.run(generator.execute_async(goals=[...], targets=[...]))
```

`GCGConfig` is composed of nested sub-configs (`GCGModelConfig`, `GCGDataConfig`,
`GCGAlgorithmConfig`, `GCGStrategyConfig`, `GCGOutputConfig`); all are re-exported
from `pyrit.executor.promptgen.gcg`. See `pyrit/executor/promptgen/gcg/config.py`
for the full surface and defaults.

### Model and tokenizer requirements

GCG currently uses local PyTorch causal language models through Hugging Face
Transformers APIs. It needs a floating-point input embedding matrix and a fast
tokenizer with a chat template that preserves message content and provides
character-to-token offsets. This is not a backend-neutral adapter interface.

Before any worker starts, GCG checks the actual training and held-out prompts
against the model's embedding rows. The initial control must also survive the
same decode-and-render round trip used for candidate controls without changing
its token IDs. The shared control decoded by the first training prompt must
also preserve those IDs in every training and held-out prompt. An incompatible
`control_init` raises an error identifying the model and control; choose a
suffix that re-encodes to the same tokens.

Worker gradients are combined only when their shapes and token/id mappings
within the embedding rows agree. These vocabulary fingerprints are cached for
the lifetime of each loaded worker, across optimization steps and progressive
phases, so its tokenizer must not be modified while that worker is in use.

### Compatibility matrix

The matrix is architecture-based. Other checkpoints with a tested architecture
and tokenizer kind are expected to work if they pass the checks above.

| Family | Tested checkpoints | Architecture (`model_type`) | Tokenizer |
| --- | --- | --- | --- |
| Qwen | Qwen2.5-0.5B-Instruct, Qwen3-0.6B | `qwen2`, `qwen3` | byte-level BPE |
| Phi | Phi-3-mini-4k-instruct, Phi-4-mini-instruct | `phi3` | SentencePiece BPE (Phi-3), byte-level BPE (Phi-4) |
| Gemma | gemma-2-2b-it, gemma-3-1b-it | `gemma2`, `gemma3_text` | SentencePiece BPE |
| Llama | TinyLlama-1.1B-Chat-v1.0, Llama-3.2-1B-Instruct | `llama` | SentencePiece BPE (TinyLlama), byte-level BPE (Llama 3.2) |
| Mistral | Mistral-7B-Instruct-v0.3, Mistral-Nemo-Instruct-2407, Mixtral-8x7B-Instruct-v0.1 | `mistral`, `mixtral` | SentencePiece BPE, byte-level BPE (Nemo) |
| SmolLM | SmolLM2-135M-Instruct, SmolLM3-3B | `llama`, `smollm3` | byte-level BPE |
| DeepSeek R1 distills | DeepSeek-R1-Distill-Qwen-1.5B, DeepSeek-R1-Distill-Llama-8B | `qwen2`, `llama` | byte-level BPE |

`tests/integration/executor/promptgen/gcg/test_gcg_compatibility_integration.py`
loads each checkpoint's real tokenizer and config, checks the goal, control, and
target slices, and loads workers through `get_workers` with a tiny randomly
initialized float16 model of the same architecture and vocabulary. It then runs
one step of each progressive phase. A mixed run trains two Qwen2.5 sizes that
share a gradient group alongside a DeepSeek distill that does not, with a
held-out TinyLlama model. These runs cover template rendering, slicing,
gradients, candidate filtering, and scoring, not attack success on the full
model. The Gemma, Llama 3.2, and
Mistral 7B/Nemo rows use unsloth's ungated mirrors, so no Hugging Face token is
needed.

### Running on Azure ML

`run.py` is a thin CLI wrapper around `GCG.execute_async`. It takes a single
`--config` flag pointing at a JSON file produced by `GCGConfig.to_json_file`:

```python
config.to_json_file("inputs/config.json")
```

```
python -m pyrit.executor.promptgen.gcg.experiments.run --config inputs/config.json
```

The notebook at `doc/code/executor/gcg/1_gcg_azure_ml.py` builds a config
locally, ships it to Azure ML as a job input, and the AML job invokes `run.py`
with the path to the deserialized JSON.

### Reference

"[Universal and Transferable Adversarial Attacks on Aligned Language Models](https://arxiv.org/abs/2307.15043)"
by Andy Zou, Zifan Wang, Nicholas Carlini, Milad Nasr, J. Zico Kolter, and Matt
Fredrikson. The paper's official Github: https://github.com/llm-attacks/llm-attacks.
