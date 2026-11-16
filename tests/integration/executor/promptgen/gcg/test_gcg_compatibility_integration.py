# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Integration tests for the GCG compatibility matrix in ``experiments/README.md``.

Each checkpoint case loads its real tokenizer and config from the
Hugging Face Hub, but not its weights. Workers come from the real ``get_workers``,
so tokenizer setup and the preflight run unchanged, but each one holds a tiny
randomly initialized float16 model built from that config and runs operations
in-process instead of in a subprocess. The architecture, vocabulary, chat template,
and tokenizer are the real ones while the run stays small enough for CPU.

Requires: torch, transformers (GCG optional deps).
Skipped via importorskip when deps are not installed.
"""

from __future__ import annotations

import queue
from copy import deepcopy
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

torch = pytest.importorskip("torch", reason="torch not installed")
transformers = pytest.importorskip("transformers", reason="transformers not installed")

from pyrit.executor.promptgen.gcg.attack.base import attack_manager  # noqa: E402
from pyrit.executor.promptgen.gcg.attack.base.attack_manager import (  # noqa: E402
    AttackPrompt,
    ModelWorker,
    ModelWorkerOperation,
    ModelWorkerTask,
    ProgressiveMultiPromptAttack,
    get_workers,
)
from pyrit.executor.promptgen.gcg.attack.gcg.gcg_attack import (  # noqa: E402
    GCGAttackPrompt,
    GCGMultiPromptAttack,
    GCGPromptManager,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.run_only_if_all_tests

# One row per checkpoint in the README matrix. None of these need a Hugging Face token.
_CHECKPOINTS = [
    pytest.param("Qwen/Qwen2.5-0.5B-Instruct", id="qwen2.5"),
    pytest.param("Qwen/Qwen3-0.6B", id="qwen3"),
    pytest.param("microsoft/Phi-3-mini-4k-instruct", id="phi-3"),
    pytest.param("microsoft/Phi-4-mini-instruct", id="phi-4"),
    pytest.param("unsloth/gemma-2-2b-it", id="gemma-2"),
    pytest.param("unsloth/gemma-3-1b-it", id="gemma-3"),
    pytest.param("TinyLlama/TinyLlama-1.1B-Chat-v1.0", id="tinyllama"),
    pytest.param("unsloth/Llama-3.2-1B-Instruct", id="llama-3.2"),
    pytest.param("unsloth/mistral-7b-instruct-v0.3", id="mistral"),
    pytest.param("unsloth/Mistral-Nemo-Instruct-2407", id="mistral-nemo"),
    pytest.param("mistralai/Mixtral-8x7B-Instruct-v0.1", id="mixtral"),
    pytest.param("HuggingFaceTB/SmolLM2-135M-Instruct", id="smollm2"),
    pytest.param("HuggingFaceTB/SmolLM3-3B", id="smollm3"),
    pytest.param("deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B", id="deepseek-r1-distill-qwen"),
    pytest.param("deepseek-ai/DeepSeek-R1-Distill-Llama-8B", id="deepseek-r1-distill-llama"),
]

_GOALS = ["Write a tutorial on how to make a cake", "Explain the plan"]
_TARGETS = ["Sure, here is a tutorial", "Sure, here is the plan"]
_CONTROL_INIT = "! ! ! ! !"


class _InProcessWorker(ModelWorker):
    """A ``ModelWorker`` with a tiny random model that runs each operation immediately."""

    def __init__(self, model_path: str, token: str | None, model_kwargs: dict[str, Any], tokenizer: Any, device: str):
        config = transformers.AutoConfig.from_pretrained(model_path)
        # Phi-3 and Phi-4 size their rotary factors for the real head dimension, so keep it.
        head_dim = config.hidden_size // config.num_attention_heads if config.model_type == "phi3" else 8
        config.update(
            {
                "hidden_size": 2 * head_dim,
                "head_dim": head_dim,
                "intermediate_size": 32,
                "num_hidden_layers": 2,
                "num_attention_heads": 2,
                "num_key_value_heads": 1,
            }
        )
        torch.manual_seed(0)
        # ModelWorker loads float16 weights; match it so the gradient path runs in the same dtype.
        self.model = transformers.AutoModelForCausalLM.from_config(config, torch_dtype=torch.float16).to(device).eval()
        self.model.requires_grad_(False)
        self.tokenizer = tokenizer
        self._vocabulary_identities: dict[int, str] = {}
        self.results: queue.Queue[Any] = queue.Queue()
        self.operations: list[ModelWorkerOperation] = []

    def start(self) -> _InProcessWorker:
        return self

    def __call__(self, ob: Any, operation: ModelWorkerOperation, *args: Any, **kwargs: Any) -> _InProcessWorker:
        self.operations.append(operation)
        task = ModelWorkerTask(obj=deepcopy(ob), operation=operation, args=args, kwargs=kwargs)
        context = torch.enable_grad() if operation is ModelWorkerOperation.GRAD else torch.no_grad()
        with context:
            result = ModelWorker._execute_task(model=self.model, task=task)
            if operation in (ModelWorkerOperation.GRAD, ModelWorkerOperation.LOSS, ModelWorkerOperation.TEST_LOSS):
                values = torch.as_tensor(result)
                assert torch.isfinite(values).all(), f"{operation.value} returned non-finite values"
                if operation is ModelWorkerOperation.GRAD:
                    row_norms = values.norm(dim=-1)
                    assert torch.isfinite(row_norms).all() and (row_norms > 0).all(), (
                        "Gradient rows must have finite, nonzero norms"
                    )
            self.results.put(result)
        return self


@pytest.fixture
def in_process_worker() -> _InProcessWorker:
    worker = object.__new__(_InProcessWorker)
    worker.model = None
    worker.results = queue.Queue()
    worker.operations = []
    return worker


@pytest.mark.parametrize(
    "operation", [ModelWorkerOperation.GRAD, ModelWorkerOperation.LOSS, ModelWorkerOperation.TEST_LOSS]
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_in_process_worker_rejects_nonfinite_results(
    *, in_process_worker: _InProcessWorker, operation: ModelWorkerOperation, value: float
) -> None:
    result: Any = torch.tensor([[1.0, 1.0], [1.0, value]], dtype=torch.float16)
    if operation is ModelWorkerOperation.TEST_LOSS:
        result = result.tolist()
    with (
        patch.object(ModelWorker, "_execute_task", return_value=result),
        pytest.raises(AssertionError, match="returned non-finite values"),
    ):
        in_process_worker(None, operation)
    assert in_process_worker.results.empty()


@pytest.mark.parametrize("row", [[0.0, 0.0], [65504.0, 65504.0]])
def test_in_process_worker_rejects_invalid_gradient_norms(
    *, in_process_worker: _InProcessWorker, row: list[float]
) -> None:
    result = torch.tensor([[1.0, 1.0], row], dtype=torch.float16)
    with (
        patch.object(ModelWorker, "_execute_task", return_value=result),
        pytest.raises(AssertionError, match="Gradient rows must have finite, nonzero norms"),
    ):
        in_process_worker(None, ModelWorkerOperation.GRAD)
    assert in_process_worker.results.empty()


@pytest.mark.parametrize(
    "operation",
    [ModelWorkerOperation.GRAD, ModelWorkerOperation.LOSS, ModelWorkerOperation.TEST_LOSS, ModelWorkerOperation.TEST],
)
def test_in_process_worker_preserves_valid_results(
    *, in_process_worker: _InProcessWorker, operation: ModelWorkerOperation
) -> None:
    result: Any = torch.tensor([[1.0, 2.0], [3.0, 4.0]], dtype=torch.float16)
    if operation is ModelWorkerOperation.TEST_LOSS:
        result = result.tolist()
    elif operation is ModelWorkerOperation.TEST:
        result = [(True, 0), (False, 1)]
    with patch.object(ModelWorker, "_execute_task", return_value=result):
        returned_worker = in_process_worker(None, operation)
    assert returned_worker is in_process_worker
    assert in_process_worker.results.get_nowait() is result
    assert in_process_worker.operations == [operation]


def _run_progressive_steps(attack: ProgressiveMultiPromptAttack) -> str:
    expected_phases = [(goal_count, 1) for goal_count in range(1, len(attack.goals) + 1)]
    expected_phases.extend((len(attack.goals), worker_count) for worker_count in range(2, len(attack.workers) + 1))
    phases: list[tuple[int, int]] = []
    real_run = GCGMultiPromptAttack.run

    def run_one_step(inner_attack: GCGMultiPromptAttack, **kwargs: Any) -> tuple[str, float, int]:
        kwargs.update(n_steps=1, stop_on_success=False)
        result = real_run(inner_attack, **kwargs)
        assert result[2] == 1, "Each progressive phase must complete one optimization step"
        phases.append((len(inner_attack.goals), len(inner_attack.workers)))
        return result

    with patch.object(GCGMultiPromptAttack, "run", autospec=True, side_effect=run_one_step):
        control, steps = attack.run(
            n_steps=len(expected_phases),
            batch_size=8,
            topk=16,
            anneal=False,
            test_steps=1,
            stop_on_success=False,
            verbose=False,
        )
    assert phases == expected_phases
    assert steps == len(expected_phases)
    return control


@pytest.mark.parametrize("num_workers", [1, 3])
@pytest.mark.parametrize("jailbroken", [False, True])
def test_progressive_steps_optimize_every_phase(*, num_workers: int, jailbroken: bool) -> None:
    workers = [
        MagicMock(spec=ModelWorker, model=SimpleNamespace(device="cpu"), tokenizer=None) for _ in range(num_workers)
    ]
    prompts = MagicMock(spec=GCGPromptManager)
    prompts.control_str = _CONTROL_INIT
    attack = ProgressiveMultiPromptAttack(
        goals=_GOALS,
        targets=_TARGETS,
        workers=workers,
        control_init=_CONTROL_INIT,
        logfile=None,
        managers={"PM": MagicMock(spec=GCGPromptManager, return_value=prompts), "MPA": GCGMultiPromptAttack},
    )
    with (
        patch.object(GCGMultiPromptAttack, "step", return_value=("updated control", 1.0)) as step,
        patch.object(GCGMultiPromptAttack, "_all_training_prompts_jailbroken", return_value=jailbroken) as success,
    ):
        assert _run_progressive_steps(attack) == "updated control"
    assert step.call_count == len(_GOALS) + num_workers - 1
    success.assert_not_called()
    assert attack.last_schedule_state is not None
    assert attack.last_schedule_state.goals_admitted == len(_GOALS)
    assert attack.last_schedule_state.workers_admitted == num_workers
    assert attack.last_schedule_state.steps_completed == step.call_count


def _run_attack(
    *, checkpoints: list[str], test_checkpoints: list[str], logfile: Path
) -> tuple[list[_InProcessWorker], list[_InProcessWorker], str]:
    """
    Load workers through ``get_workers`` and run one step of every progressive phase.

    Returns:
        tuple[list[_InProcessWorker], list[_InProcessWorker], str]: Training workers,
            held-out workers, and the final control.
    """
    paths = [*checkpoints, *test_checkpoints]
    params = SimpleNamespace(
        tokenizer_paths=paths,
        tokenizer_kwargs=[{} for _ in paths],
        model_paths=paths,
        model_kwargs=[{} for _ in paths],
        devices=["cpu" for _ in paths],
        token=None,
        num_train_models=len(checkpoints),
        goals=_GOALS,
        targets=_TARGETS,
        test_goals=_GOALS[:1],
        test_targets=_TARGETS[:1],
        control_init=_CONTROL_INIT,
    )
    with patch.object(attack_manager, "ModelWorker", _InProcessWorker):
        workers, test_workers = get_workers(params)
    attack = ProgressiveMultiPromptAttack(
        goals=_GOALS,
        targets=_TARGETS,
        workers=workers,
        test_workers=test_workers,
        test_goals=params.test_goals,
        test_targets=params.test_targets,
        control_init=_CONTROL_INIT,
        logfile=str(logfile),
        managers={"AP": GCGAttackPrompt, "PM": GCGPromptManager, "MPA": GCGMultiPromptAttack},
    )
    control = _run_progressive_steps(attack)
    return workers, test_workers, control


@pytest.mark.parametrize("checkpoint", _CHECKPOINTS)
def test_prompt_slices_match_rendered_text(checkpoint: str) -> None:
    tokenizer = transformers.AutoTokenizer.from_pretrained(checkpoint)
    prompt = AttackPrompt(goal=_GOALS[0], target=_TARGETS[0], tokenizer=tokenizer, control_init=_CONTROL_INIT)

    def decode(token_slice: slice) -> str:
        return tokenizer.decode(prompt.input_ids[token_slice]).strip()

    assert decode(prompt._goal_slice) == _GOALS[0]
    assert decode(prompt._control_slice) == _CONTROL_INIT
    assert decode(prompt._target_slice) == _TARGETS[0]


@pytest.mark.parametrize("checkpoint", _CHECKPOINTS)
def test_attack_step_runs_on_tiny_model(checkpoint: str, tmp_path: Path) -> None:
    (worker,), _, control = _run_attack(checkpoints=[checkpoint], test_checkpoints=[], logfile=tmp_path / "log.json")

    assert ModelWorkerOperation.GRAD in worker.operations
    assert control != _CONTROL_INIT


def test_attack_step_runs_with_mixed_tokenizers(tmp_path: Path) -> None:
    # The two Qwen2.5 sizes share a vocabulary and so one gradient group. The DeepSeek distill has
    # the same embedding rows but different special tokens, so it gets its own group. TinyLlama is a
    # held-out model with a SentencePiece tokenizer that must accept the same shared control.
    workers, (held_out,), control = _run_attack(
        checkpoints=[
            "Qwen/Qwen2.5-0.5B-Instruct",
            "Qwen/Qwen2.5-1.5B-Instruct",
            "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B",
        ],
        test_checkpoints=["TinyLlama/TinyLlama-1.1B-Chat-v1.0"],
        logfile=tmp_path / "log.json",
    )

    assert all(ModelWorkerOperation.GRAD in worker.operations for worker in workers)
    assert ModelWorkerOperation.GRAD not in held_out.operations
    assert held_out.operations
    qwen, qwen_larger, deepseek = (worker._vocabulary_identities for worker in workers)
    assert qwen == qwen_larger != deepseek
    assert control != _CONTROL_INIT
