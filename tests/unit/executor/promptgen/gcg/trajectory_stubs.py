# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Deterministic in-process stand-ins for GCG model workers and prompt managers.

They let the real ``GCGMultiPromptAttack.step()`` (sampling, filtering, loss,
selection) and the real ``MultiPromptAttack.run()`` (annealing, logging) execute
end to end without loading a model. Every model output (gradient, logits) is a
pure function of its inputs, so the only randomness left in a run is the seeded
RNG bundle under test.
"""

from __future__ import annotations

import queue
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import torch

from pyrit.executor.promptgen.gcg.attack.base.attack_manager import ModelWorkerOperation
from pyrit.executor.promptgen.gcg.attack.gcg.gcg_attack import GCGMultiPromptAttack

if TYPE_CHECKING:
    import threading

VOCAB_SIZE = 16


def _seed(*parts: int) -> int:
    # Hashing a tuple of ints is deterministic in CPython; str hash randomization is not involved.
    return hash(parts) & 0xFFFF_FFFF


class IntTokenizer:
    """Tokens are ints rendered as space-joined text; ``"!"`` is token 0."""

    vocab_size = VOCAB_SIZE
    name_or_path = "int-tokenizer"
    chat_template = None

    def encode_ints(self, text: str) -> list[int]:
        return [0 if tok == "!" else int(tok) for tok in text.split()]

    def __call__(self, text: str, **_: Any) -> SimpleNamespace:
        return SimpleNamespace(input_ids=self.encode_ints(text))

    def decode(self, token_ids: torch.Tensor, **_: Any) -> str:
        return " ".join(str(int(t)) for t in token_ids)


class _Prompt:
    def __init__(self, *, target_ids: list[int], control_len: int) -> None:
        self.target_ids = target_ids
        self._control_slice = slice(1, 1 + control_len)
        self._target_slice = slice(1 + control_len, 1 + control_len + len(target_ids))


class TrajectoryPromptManager:
    """The subset of the ``PromptManager`` contract that ``GCGMultiPromptAttack.step()`` touches."""

    def __init__(
        self,
        goals: list[str],
        targets: list[str],
        tokenizer: IntTokenizer,
        control_init: str,
        test_prefixes: list[str] | None = None,
        managers: dict[str, Any] | None = None,
    ) -> None:
        self.tokenizer = tokenizer
        self.control_init = control_init
        self.control_str = control_init
        self._prompts = [
            _Prompt(target_ids=tokenizer.encode_ints(t), control_len=len(self.control_toks)) for t in targets
        ]
        self.disallowed_toks = torch.empty(0, dtype=torch.long)

    @property
    def control_str(self) -> str:
        return self._control_str

    @control_str.setter
    def control_str(self, control: str) -> None:
        self._control_str = control
        self.control_toks = torch.tensor(self.tokenizer.encode_ints(control), dtype=torch.long)

    def __len__(self) -> int:
        return len(self._prompts)

    def __getitem__(self, i: int) -> _Prompt:
        return self._prompts[i]

    def __iter__(self) -> Any:
        return iter(self._prompts)


class TrajectoryWorker:
    """
    Synchronous stand-in for ``ModelWorker`` whose outputs are determined by their inputs.

    GRAD returns a gradient that is a pure function of (worker id, control tokens), with
    the current token at every slot pushed to +100 so top-k sampling never reproduces the
    current control and the length-preserving filter never runs dry. LOGITS returns
    per-candidate logits that are a pure function of the candidate's token ids. TEST reports
    a jailbreak once the control has moved off its initial value, so progressive phases
    advance after one accepted step.

    When ``barrier`` is shared by two concurrently running attacks, worker 0 waits on it once
    per optimization step, forcing the two runs to interleave. Each crossing appends ``run_id``
    to ``events`` so tests can assert that the interleaving actually happened.
    """

    def __init__(
        self,
        worker_id: int = 0,
        *,
        run_id: str = "",
        barrier: threading.Barrier | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.run_id = run_id
        self.barrier = barrier
        self.events = events
        self.tokenizer = IntTokenizer()
        self.model = SimpleNamespace(device=torch.device("cpu"), name_or_path=f"stub-model-{worker_id}")
        self.results: queue.SimpleQueue[Any] = queue.SimpleQueue()

    def start(self) -> TrajectoryWorker:
        return self

    def stop(self) -> TrajectoryWorker:
        return self

    def __call__(self, ob: Any, operation: ModelWorkerOperation, *args: Any, **kwargs: Any) -> TrajectoryWorker:
        self.results.put(self._execute(ob, operation, *args))
        return self

    def _execute(self, ob: Any, operation: ModelWorkerOperation, *args: Any) -> Any:
        if operation is ModelWorkerOperation.GRAD:
            if self.barrier is not None and self.worker_id == 0:
                self.barrier.wait()
                if self.events is not None:
                    self.events.append(self.run_id)
            return self._grad(ob.control_toks)
        if operation is ModelWorkerOperation.LOGITS:
            return self._logits(ob, args[0])
        if operation is ModelWorkerOperation.TEST:
            return [(ob.control_str != ob.control_init, 0) for _ in ob]
        if operation is ModelWorkerOperation.TEST_LOSS:
            return [0.0 for _ in ob]
        raise NotImplementedError(operation)

    def _grad(self, control_toks: torch.Tensor) -> torch.Tensor:
        gen = torch.Generator().manual_seed(_seed(self.worker_id, *control_toks.tolist()))
        grad = torch.randn(len(control_toks), VOCAB_SIZE, generator=gen)
        grad[torch.arange(len(control_toks)), control_toks] = 100.0
        return grad

    def _logits(self, prompt: _Prompt, candidates: list[str]) -> tuple[torch.Tensor, torch.Tensor]:
        rows = [[0, *self.tokenizer.encode_ints(c), *prompt.target_ids] for c in candidates]
        ids = torch.tensor(rows, dtype=torch.long)
        logits = torch.stack(
            [
                torch.randn(
                    ids.shape[1], VOCAB_SIZE, generator=torch.Generator().manual_seed(_seed(self.worker_id, *row))
                )
                for row in rows
            ]
        )
        return logits, ids


class RecordingGCGAttack(GCGMultiPromptAttack):
    """The real GCG attack, recording sampled candidates, per-step decisions and the bundle each step used."""

    def __init__(self, *args: Any, events: list[Any], bundles: list[Any] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.events = events
        self.bundles = bundles
        events.append(("attack", tuple(self.goals), len(self.workers)))

    def _sample_control_candidates(self, **kwargs: Any) -> torch.Tensor:
        candidates = super()._sample_control_candidates(**kwargs)
        self.events.append(("candidates", tuple(map(tuple, candidates.tolist()))))
        return candidates

    def step(self, **kwargs: Any) -> tuple[str, float]:
        if self.bundles is not None:
            self.bundles.append(getattr(self, "_rng_bundle", None))
        before = self.control_str
        control, loss = super().step(**kwargs)
        self.events.append(("step", before, control, round(loss, 6)))
        return control, loss
