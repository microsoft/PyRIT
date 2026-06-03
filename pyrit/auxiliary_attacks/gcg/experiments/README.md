## GCG Experiments

This directory contains the public entry point for running the [Greedy Coordinate
Gradient (GCG) attack](https://arxiv.org/abs/2307.15043).

### Public API

The primary entry point is `GCG.execute_async` (`GCG` is an alias for
`GCGGenerator`):

```python
import asyncio

from pyrit.auxiliary_attacks.gcg import GCG, GCGModelConfig

generator = GCG(
    models=[GCGModelConfig(name="meta-llama/Llama-2-7b-chat-hf")],
)
result = asyncio.run(generator.execute_async(goals=[...], targets=[...]))
```

`GCGConfig` is composed of nested sub-configs (`GCGModelConfig`, `GCGDataConfig`,
`GCGAlgorithmConfig`, `GCGStrategyConfig`, `GCGOutputConfig`); all are re-exported
from `pyrit.auxiliary_attacks.gcg`. See `pyrit/auxiliary_attacks/gcg/config.py`
for the full surface and defaults.

### Running on Azure ML

`run.py` is a thin CLI wrapper around `GCG.execute_async`. It takes a single
`--config` flag pointing at a JSON file produced by `GCGConfig.to_json_file`:

```python
config.to_json_file("inputs/config.json")
```

```
python -m pyrit.auxiliary_attacks.gcg.experiments.run --config inputs/config.json
```

The notebook at `doc/code/auxiliary_attacks/1_gcg_azure_ml.py` builds a config
locally, ships it to Azure ML as a job input, and the AML job invokes `run.py`
with the path to the deserialized JSON.

### Reference

"[Universal and Transferable Adversarial Attacks on Aligned Language Models](https://arxiv.org/abs/2307.15043)"
by Andy Zou, Zifan Wang, Nicholas Carlini, Milad Nasr, J. Zico Kolter, and Matt
Fredrikson. The paper's official Github: https://github.com/llm-attacks/llm-attacks.
