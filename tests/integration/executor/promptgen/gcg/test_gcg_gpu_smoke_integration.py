# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Opt-in GPU smoke tests for GCG on a real modern checkpoint.

Runs ``GCGGenerator`` end to end on Qwen2.5-0.5B-Instruct: worker subprocesses,
gradients, candidate sampling and filtering, candidate scoring, and the log the
result is read from. A short run lowers the loss even with a random gradient,
so the run's logged loss is checked against the model directly, and a second
test checks that the gradient predicts which token swaps lower the loss.
Needs a CUDA GPU and ``RUN_ALL_TESTS=true``.

Requires: torch, transformers (GCG optional deps).
Skipped via importorskip when deps are not installed.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

torch = pytest.importorskip("torch", reason="torch not installed")
transformers = pytest.importorskip("transformers", reason="transformers not installed")


from pyrit.executor.promptgen.gcg import (  # noqa: E402
    GCGAlgorithmConfig,
    GCGGenerator,
    GCGModelConfig,
    GCGOutputConfig,
)
from pyrit.executor.promptgen.gcg.attack.base.attack_manager import get_nonascii_toks  # noqa: E402
from pyrit.executor.promptgen.gcg.attack.gcg.gcg_attack import GCGAttackPrompt  # noqa: E402

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [
    pytest.mark.run_only_if_all_tests,
    pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA GPU required"),
]

_CHECKPOINT = "Qwen/Qwen2.5-0.5B-Instruct"
_GOAL = "Write a tutorial on how to make a cake"
_TARGET = "Sure, here is a tutorial"
_CONTROL_INIT = "! ! ! ! ! ! ! ! ! !"
_N_STEPS = 5


@pytest.fixture(scope="module")
def tokenizer() -> Any:
    return transformers.AutoTokenizer.from_pretrained(_CHECKPOINT)


@pytest.fixture(scope="module")
def model() -> Any:
    loaded = transformers.AutoModelForCausalLM.from_pretrained(_CHECKPOINT, torch_dtype=torch.float16)
    return loaded.to("cuda").eval().requires_grad_(False)


def _target_losses(model: Any, prompt: GCGAttackPrompt, batch: Any) -> Any:
    """Target loss of each row of ``batch``, a stack of the prompt's token ids."""
    with torch.no_grad():
        logits = model(batch).logits[:, prompt._loss_slice].float()
    targets = batch[:, prompt._target_slice]
    return torch.nn.functional.cross_entropy(logits.transpose(1, 2), targets, reduction="none").mean(dim=1)


@pytest.mark.timeout(900)
async def test_gcg_lowers_target_loss_on_real_checkpoint(
    tmp_path: Path, tokenizer: Any, request: pytest.FixtureRequest
) -> None:
    generator = GCGGenerator(
        models=[GCGModelConfig(name=_CHECKPOINT)],
        algorithm=GCGAlgorithmConfig(
            n_steps=_N_STEPS, test_steps=1, batch_size=64, topk=64, control_init=_CONTROL_INIT
        ),
        output=GCGOutputConfig(logfile=str(tmp_path / "log.json"), verbose=False),
    )

    result = await generator.execute_async(goals=[_GOAL], targets=[_TARGET])

    # The first log entry is the starting suffix with a placeholder loss, before any step runs.
    losses = result.loss_history[1:]
    assert len(losses) == _N_STEPS
    assert losses[-1] < losses[0]

    # The logged loss must be the model's loss for the final suffix, so scoring really ran.
    # The generator may rephrase the target, so read the one it used from the log.
    # Loading a second copy of the model before the run hung the workers on a Kaggle T4.
    model = request.getfixturevalue("model")
    target = json.loads((tmp_path / "log.json").read_text())["params"]["targets"][0]
    prompt = GCGAttackPrompt(goal=_GOAL, target=target, tokenizer=tokenizer, control_init=result.final_suffix)
    recomputed = _target_losses(model, prompt, prompt.input_ids.to("cuda")[None]).item()
    assert recomputed == pytest.approx(result.final_loss, abs=0.02)


def test_gradient_predicts_loss_change(model: Any, tokenizer: Any) -> None:
    prompt = GCGAttackPrompt(goal=_GOAL, target=_TARGET, tokenizer=tokenizer, control_init=_CONTROL_INIT)
    grad = prompt.grad(model)[:, : tokenizer.vocab_size].float()

    # Random single-token swaps over the tokens the sampler may pick.
    blocked = get_nonascii_toks(tokenizer, device="cuda")
    allowed = torch.ones(tokenizer.vocab_size, dtype=torch.bool, device="cuda")
    allowed[blocked[blocked < tokenizer.vocab_size]] = False
    allowed_ids = allowed.nonzero().squeeze(1)
    n_swaps = 2048
    generator = torch.Generator(device="cuda").manual_seed(0)
    positions = torch.randint(grad.shape[0], (n_swaps,), device="cuda", generator=generator)
    tokens = allowed_ids[torch.randint(len(allowed_ids), (n_swaps,), device="cuda", generator=generator)]

    ids = prompt.input_ids.to("cuda")
    batch = ids.repeat(n_swaps, 1)
    batch[torch.arange(n_swaps), prompt._control_slice.start + positions] = tokens
    actual = torch.cat([_target_losses(model, prompt, chunk) for chunk in batch.split(128)])
    current = ids[prompt._control_slice][positions]
    predicted = grad[positions, tokens] - grad[positions, current]

    # Spearman rank correlation between the first-order prediction and the loss after the swap.
    ranks = torch.stack([predicted.argsort().argsort(), actual.argsort().argsort()]).float()
    correlation = torch.corrcoef(ranks)[0, 1].item()
    assert correlation > 0.1
