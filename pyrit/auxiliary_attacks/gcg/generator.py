# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""GCGGenerator — typed PromptGeneratorStrategy implementation of the
Greedy Coordinate Gradient adversarial-suffix attack.

Follows the same lifecycle/identity pattern as ``FuzzerGenerator`` and
``AnecdoctorGenerator``:

- Strategy configuration goes in ``__init__`` (model identity, hyper-parameters,
  strategy flags, output paths).
- Per-execution data (``goals`` / ``targets`` plus optional held-out splits and
  ``memory_labels``) goes through ``execute_async``.
- ``_setup_async`` / ``_perform_async`` / ``_teardown_async`` cleanly split
  worker spawning, the optimization loop, and worker shutdown. Teardown runs
  even on errors, fixing the worker-leak-on-failure case the previous lifecycle
  tests had to characterize as a known bug.
- ``_build_identifier`` exposes the model name(s) and key hyper-parameters so
  results can be traced back to the exact configuration that produced them.

Example:

    generator = GCGGenerator(
        models=[GCGModelConfig(name="meta-llama/Llama-2-7b-chat-hf")],
        algorithm=GCGAlgorithmConfig(n_steps=500, batch_size=512),
    )
    result = await generator.execute_async(
        goals=["how do I ..."],
        targets=["Sure, here is ..."],
    )
    print(result.final_suffix)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from functools import partial
from typing import Any, overload

import numpy as np
import torch.multiprocessing as mp
from pydantic import Field

import pyrit.auxiliary_attacks.gcg.attack.gcg.gcg_attack as attack_lib
from pyrit.auxiliary_attacks.gcg.attack.base.attack_manager import (
    IndividualPromptAttack,
    ProgressiveMultiPromptAttack,
    get_workers,
)
from pyrit.auxiliary_attacks.gcg.config import (
    GCGAlgorithmConfig,
    GCGModelConfig,
    GCGOutputConfig,
    GCGStrategyConfig,
)
from pyrit.auxiliary_attacks.gcg.experiments.log import log_gpu_memory, log_train_goals
from pyrit.common.utils import combine_dict
from pyrit.executor.promptgen.core.prompt_generator_strategy import (
    PromptGeneratorStrategy,
    PromptGeneratorStrategyContext,
    PromptGeneratorStrategyResult,
)
from pyrit.models import ComponentIdentifier, Identifiable

logger = logging.getLogger(__name__)


@dataclass
class GCGContext(PromptGeneratorStrategyContext):
    """Per-execution state for a GCGGenerator run.

    Attributes:
        goals (list[str]): Training goal strings (the prompts whose responses
            we are trying to redirect). Must be non-empty.
        targets (list[str]): Training target strings (the desired prefixes of
            the model's responses). Same length as ``goals``.
        test_goals (list[str]): Optional held-out goals for evaluation only.
            Defaults to an empty list.
        test_targets (list[str]): Optional held-out targets matching
            ``test_goals``. Same length as ``test_goals``.
        memory_labels (dict[str, str]): Optional labels propagated to memory
            for downstream filtering / analysis. Defaults to an empty dict.
    """

    goals: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    test_goals: list[str] = field(default_factory=list)
    test_targets: list[str] = field(default_factory=list)
    memory_labels: dict[str, str] = field(default_factory=dict)

    workers: list[Any] = field(default_factory=list)
    test_workers: list[Any] = field(default_factory=list)
    attack: Any | None = None
    logfile_path: str | None = None


class GCGResult(PromptGeneratorStrategyResult):
    """Result of one GCGGenerator run.

    Attributes:
        final_suffix (str): The optimized adversarial suffix string. Empty
            string means the run produced no candidates (degenerate config).
        final_loss (float): Loss at the final step. ``float('nan')`` if no
            losses were recorded.
        step_count (int): Number of optimization steps actually executed.
        loss_history (list[float]): Per-step loss values.
        control_history (list[str]): Per-step suffix candidates.
        log_path (str | None): Filesystem path of the JSON log written during
            the run, or ``None`` if logging was disabled.
        memory_labels (dict[str, str]): Echo of the labels passed in via the
            context, kept on the result for traceability.
    """

    final_suffix: str = ""
    final_loss: float = float("nan")
    step_count: int = 0
    loss_history: list[float] = Field(default_factory=list)
    control_history: list[str] = Field(default_factory=list)
    log_path: str | None = None
    memory_labels: dict[str, str] = Field(default_factory=dict)


class GCGGenerator(
    PromptGeneratorStrategy[GCGContext, GCGResult],
    Identifiable,
):
    """Greedy Coordinate Gradient adversarial-suffix generator.

    Generates a token suffix that, when appended to ``goals``, is optimized to
    elicit ``targets`` from the configured HuggingFace model(s). See the GCG
    paper ([@zou2023gcg](https://arxiv.org/abs/2307.15043)) for the algorithm.
    """

    def __init__(
        self,
        *,
        models: list[GCGModelConfig],
        algorithm: GCGAlgorithmConfig | None = None,
        strategy: GCGStrategyConfig | None = None,
        output: GCGOutputConfig | None = None,
        test_models: list[GCGModelConfig] | None = None,
        hf_token: str | None = None,
    ) -> None:
        """
        Initialize the GCG generator.

        Args:
            models (list[GCGModelConfig]): Training models the attack is
                optimized against. Must be non-empty. For the standard
                single-model attack pass a one-element list; transfer attacks
                pass several.
            algorithm (GCGAlgorithmConfig | None): Optimization
                hyper-parameters. ``None`` uses dataclass defaults.
            strategy (GCGStrategyConfig | None): High-level strategy flags
                (transfer / progressive / anneal / stop_on_success). ``None``
                uses dataclass defaults.
            output (GCGOutputConfig | None): Log/result file locations.
                ``None`` uses dataclass defaults.
            test_models (list[GCGModelConfig] | None): Held-out models used
                for evaluation only. Defaults to an empty list.
            hf_token (str | None): HuggingFace authentication token. Defaults
                to ``None`` (the model loader falls back to whatever the
                environment provides).

        Raises:
            ValueError: If ``models`` is empty.
        """
        super().__init__(logger=logger, context_type=GCGContext)
        if not models:
            raise ValueError("GCGGenerator: 'models' must contain at least one GCGModelConfig.")
        self._models = list(models)
        self._test_models = list(test_models or [])
        self._algorithm = algorithm or GCGAlgorithmConfig()
        self._strategy = strategy or GCGStrategyConfig()
        self._output = output or GCGOutputConfig()
        self._hf_token = hf_token

    def _ensure_spawn_start_method(self) -> None:
        """Ensure torch.multiprocessing uses 'spawn' before workers are spawned.

        GCG workers load CUDA models, which is unsafe with the default 'fork'
        start method on Linux. We set 'spawn' on the first GCG run in the
        interpreter; if some earlier code already configured a different method
        (e.g. another test in a long-running pytest session) we log a warning
        rather than crash, since changing the global start method out from under
        unrelated code is worse than running with the existing setting.
        """
        current = mp.get_start_method(allow_none=True)
        if current is None:
            mp.set_start_method("spawn")
        elif current != "spawn":
            self._logger.warning(
                "torch.multiprocessing start method is already %r, not 'spawn'. "
                "GCG workers load CUDA models and expect 'spawn'; results may be "
                "unreliable. Configure 'spawn' before any other multiprocessing "
                "code runs to silence this warning.",
                current,
            )

    def _build_identifier(self) -> ComponentIdentifier:
        """Build a behavioral identifier exposing model identity + key hyper-params."""
        return ComponentIdentifier.of(
            self,
            params={
                "models": [m.name for m in self._models],
                "test_models": [m.name for m in self._test_models],
                "n_steps": self._algorithm.n_steps,
                "batch_size": self._algorithm.batch_size,
                "topk": self._algorithm.topk,
                "target_weight": self._algorithm.target_weight,
                "control_weight": self._algorithm.control_weight,
                "sampling_impl": (
                    type(self._algorithm.sampling).__name__ if self._algorithm.sampling is not None else "default"
                ),
                "loss_impl": type(self._algorithm.loss).__name__ if self._algorithm.loss is not None else "default",
                "candidate_filter_impl": (
                    type(self._algorithm.candidate_filter).__name__
                    if self._algorithm.candidate_filter is not None
                    else "default"
                ),
                "suffix_init_impl": (
                    type(self._algorithm.suffix_init).__name__ if self._algorithm.suffix_init is not None else "default"
                ),
                "transfer": self._strategy.transfer,
                "progressive_goals": self._strategy.progressive_goals,
                "progressive_models": self._strategy.progressive_models,
                "anneal": self._strategy.anneal,
                "incr_control": self._strategy.incr_control,
                "stop_on_success": self._strategy.stop_on_success,
            },
        )

    def _validate_context(self, *, context: GCGContext) -> None:
        if not context.goals:
            raise ValueError("GCGContext.goals must be non-empty.")
        if not context.targets:
            raise ValueError("GCGContext.targets must be non-empty.")
        if len(context.goals) != len(context.targets):
            raise ValueError(f"goals/targets length mismatch: {len(context.goals)} vs {len(context.targets)}")
        if len(context.test_goals) != len(context.test_targets):
            raise ValueError(
                f"test_goals/test_targets length mismatch: {len(context.test_goals)} vs {len(context.test_targets)}"
            )

    async def _setup_async(self, *, context: GCGContext) -> None:
        """Apply target augmentation and spawn worker subprocesses."""
        self._ensure_spawn_start_method()
        context.memory_labels = combine_dict({}, context.memory_labels)

        context.targets, context.test_targets = self._apply_target_augmentation(
            train_targets=context.targets,
            test_targets=context.test_targets,
        )

        log_gpu_memory(step=0)
        log_train_goals(train_goals=context.goals)

        params = self._to_attack_params(context=context)
        context.workers, context.test_workers = await asyncio.to_thread(get_workers, params)

    async def _perform_async(self, *, context: GCGContext) -> GCGResult:
        """Build the attack, run the optimization loop, and read the result back."""
        params = self._to_attack_params(context=context)
        context.logfile_path = self._build_logfile_path()

        managers = {
            "AP": attack_lib.GCGAttackPrompt,
            "PM": attack_lib.GCGPromptManager,
            "MPA": partial(
                attack_lib.GCGMultiPromptAttack,
                sampling=self._algorithm.sampling,
                loss=self._algorithm.loss,
                candidate_filter=self._algorithm.candidate_filter,
            ),
        }
        context.attack = self._create_attack(
            params=params,
            managers=managers,
            train_goals=context.goals,
            train_targets=context.targets,
            test_goals=context.test_goals,
            test_targets=context.test_targets,
            workers=context.workers,
            test_workers=context.test_workers,
            logfile_path=context.logfile_path,
        )

        await asyncio.to_thread(
            context.attack.run,
            n_steps=self._algorithm.n_steps,
            batch_size=self._algorithm.batch_size,
            topk=self._algorithm.topk,
            temp=self._algorithm.temp,
            target_weight=self._algorithm.target_weight,
            control_weight=self._algorithm.control_weight,
            test_steps=self._algorithm.test_steps,
            anneal=self._strategy.anneal,
            incr_control=self._strategy.incr_control,
            stop_on_success=self._strategy.stop_on_success,
            verbose=self._output.verbose,
            filter_cand=self._algorithm.filter_cand,
            allow_non_ascii=self._algorithm.allow_non_ascii,
        )

        return self._read_result(logfile_path=context.logfile_path, memory_labels=context.memory_labels)

    async def _teardown_async(self, *, context: GCGContext) -> None:
        """Stop every worker subprocess. Runs even when _perform_async raises."""
        for worker in list(context.workers) + list(context.test_workers):
            try:
                await asyncio.to_thread(worker.stop)
            except Exception as e:
                self._logger.warning(f"Failed to stop worker {worker!r}: {e}")
        context.workers = []
        context.test_workers = []
        context.attack = None

    @overload
    async def execute_async(
        self,
        *,
        goals: list[str],
        targets: list[str],
        test_goals: list[str] | None = None,
        test_targets: list[str] | None = None,
        memory_labels: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> GCGResult: ...

    @overload
    async def execute_async(self, **kwargs: Any) -> GCGResult: ...

    async def execute_async(self, **kwargs: Any) -> GCGResult:
        """
        Run the GCG attack with the given goal/target lists.

        Args:
            goals (list[str]): Training goal strings. Required.
            targets (list[str]): Training target prefixes. Required, same
                length as ``goals``.
            test_goals (list[str] | None): Optional held-out goals.
            test_targets (list[str] | None): Optional held-out targets.
            memory_labels (dict[str, str] | None): Optional labels echoed onto
                the result.
            **kwargs: Forwarded to the base ``Strategy.execute_async``.

        Returns:
            GCGResult: The optimization result.
        """
        kwargs.setdefault("test_goals", [])
        kwargs.setdefault("test_targets", [])
        kwargs.setdefault("memory_labels", {})
        return await super().execute_async(**kwargs)

    def _build_logfile_path(self) -> str:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        if self._output.logfile:
            return self._output.logfile
        return f"{self._output.result_prefix}_{timestamp}.json"

    @staticmethod
    def _apply_target_augmentation(
        *,
        train_targets: list[str],
        test_targets: list[str],
    ) -> tuple[list[str], list[str]]:
        """Randomly substitute equivalent target phrasings for diversity."""

        def _shorten(s: str) -> str:
            return s.replace("Sure, h", "H")

        def _contract(s: str) -> str:
            return s.replace("Sure, here is", "Sure, here's")

        train_targets = [_shorten(t) if np.random.random() < 0.5 else _contract(t) for t in train_targets]
        test_targets = [_shorten(t) if np.random.random() < 0.5 else _contract(t) for t in test_targets]
        return train_targets, test_targets

    def _to_attack_params(self, *, context: GCGContext) -> Any:
        """Build the dotted-attribute namespace the internal helpers expect."""
        from types import SimpleNamespace

        all_models = self._models + self._test_models
        return SimpleNamespace(
            token=self._hf_token or "",
            tokenizer_paths=[m.name for m in all_models],
            tokenizer_kwargs=[m.tokenizer_kwargs for m in all_models],
            model_paths=[m.name for m in all_models],
            model_kwargs=[m.model_kwargs for m in all_models],
            devices=[m.device for m in all_models],
            num_train_models=len(self._models),
            random_seed=self._algorithm.random_seed,
            train_data="",
            test_data="",
            n_train_data=len(context.goals),
            n_test_data=len(context.test_goals),
            goals=context.goals,
            targets=context.targets,
            test_goals=context.test_goals,
            test_targets=context.test_targets,
        )

    def _create_attack(
        self,
        *,
        params: Any,
        managers: dict[str, Any],
        train_goals: list[str],
        train_targets: list[str],
        test_goals: list[str],
        test_targets: list[str],
        workers: list[Any],
        test_workers: list[Any],
        logfile_path: str,
    ) -> Any:
        """Build the right attack object based on the strategy flags."""
        control_init = self._resolve_control_init(workers=workers)
        if self._strategy.transfer:
            return ProgressiveMultiPromptAttack(
                train_goals,
                train_targets,
                workers,
                progressive_models=self._strategy.progressive_models,
                progressive_goals=self._strategy.progressive_goals,
                control_init=control_init,
                logfile=logfile_path,
                managers=managers,
                test_goals=test_goals,
                test_targets=test_targets,
                test_workers=test_workers,
                mpa_lr=self._algorithm.learning_rate,
                mpa_batch_size=self._algorithm.batch_size,
                mpa_n_steps=self._algorithm.n_steps,
            )
        return IndividualPromptAttack(
            train_goals,
            train_targets,
            workers,
            control_init=control_init,
            logfile=logfile_path,
            managers=managers,
            test_goals=test_goals,
            test_targets=test_targets,
            test_workers=test_workers,
            mpa_lr=self._algorithm.learning_rate,
            mpa_batch_size=self._algorithm.batch_size,
            mpa_n_steps=self._algorithm.n_steps,
        )

    def _resolve_control_init(self, *, workers: list[Any]) -> str:
        """Resolve the initial suffix string for a run.

        Uses the configured ``suffix_init`` extension when provided; otherwise
        falls back to the legacy literal ``control_init`` value.
        """
        if self._algorithm.suffix_init is None:
            return self._algorithm.control_init
        if not workers:
            raise ValueError("Cannot resolve suffix_init without at least one worker tokenizer.")
        return self._algorithm.suffix_init.make_initial_suffix(tokenizer=workers[0].tokenizer)

    @staticmethod
    def _read_result(*, logfile_path: str, memory_labels: dict[str, str]) -> GCGResult:
        """Pull final-step values out of the JSON log written during the run."""
        try:
            with open(logfile_path) as f:
                log = json.load(f)
        except FileNotFoundError:
            logger.warning(f"GCG logfile not found at {logfile_path}; returning empty result.")
            return GCGResult(memory_labels=dict(memory_labels), log_path=None)

        controls = log.get("controls", []) or []
        losses = log.get("losses", []) or []
        return GCGResult(
            final_suffix=controls[-1] if controls else "",
            final_loss=losses[-1] if losses else float("nan"),
            step_count=len(controls),
            loss_history=list(losses),
            control_history=list(controls),
            log_path=logfile_path,
            memory_labels=dict(memory_labels),
        )
