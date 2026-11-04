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
its token IDs. An incompatible `control_init` raises an error identifying the
model and control; choose a suffix that re-encodes to the same tokens.

Worker gradients are combined only when their shapes and token/id mappings
within the embedding rows agree. These vocabulary fingerprints are cached for
the lifetime of the candidate proposer, so tokenizers must not be modified
during an optimization run.

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
