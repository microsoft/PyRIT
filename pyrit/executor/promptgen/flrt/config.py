# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Typed configuration objects for the Fluent Student-Teacher Redteaming (FLRT) attack.

A minimal call is::

    generator = FLRTGenerator(
        model=FLRTModelConfig(name="meta-llama/Llama-3-8B-Instruct"),
    )
    await generator.execute_async(goals=[...], targets=[...])

Each sub-config (``FLRTModelConfig``, ``FLRTAlgorithmConfig``,
``FLRTOutputConfig``) ships with defaults that match the FLRT paper
([@thompson2024flrt](https://arxiv.org/abs/2407.17447)), so most callers only
need to set the model name plus whichever hyper-parameters they want to
override.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _default_model_kwargs() -> dict[str, Any]:
    return {"low_cpu_mem_usage": True, "use_cache": False}


def _default_tokenizer_kwargs() -> dict[str, Any]:
    return {"use_fast": True}


# Mutation probabilities used before ``schedule_step`` and after it,
# ordered (delete, insert, swap, edge). The paper's recommended schedule
# biases toward edge insertions early so a short or empty initial attack
# grows quickly, then disables edge insertions.
_DEFAULT_EARLY_UPDATE_PROBS: tuple[float, float, float, float] = (0.25, 0.25, 0.25, 0.25)
_DEFAULT_LATE_UPDATE_PROBS: tuple[float, float, float, float] = (1 / 3, 1 / 3, 1 / 3, 0.0)


@dataclass
class FLRTModelConfig:
    """
    Identity and loading options for the HuggingFace model used by FLRT.

    Attributes:
        name (str): HuggingFace model identifier such as
            ``"meta-llama/Llama-3-8B-Instruct"``. The same string is used to
            load both the model weights and the tokenizer.
        device (str): Torch device string for this model. Defaults to
            ``"cuda:0"``.
        model_kwargs (dict[str, Any]): Extra keyword arguments forwarded to
            ``AutoModelForCausalLM.from_pretrained``. Defaults to
            ``{"low_cpu_mem_usage": True, "use_cache": False}``.
        tokenizer_kwargs (dict[str, Any]): Extra keyword arguments forwarded to
            ``AutoTokenizer.from_pretrained``. Defaults to ``{"use_fast": True}``
            because FLRT needs the character offsets only fast tokenizers provide.
    """

    name: str
    device: str = "cuda:0"
    model_kwargs: dict[str, Any] = field(default_factory=_default_model_kwargs)
    tokenizer_kwargs: dict[str, Any] = field(default_factory=_default_tokenizer_kwargs)

    def __post_init__(self) -> None:
        """
        Validate the model identifier.

        Raises:
            ValueError: If the model identifier is empty.
        """
        if not self.name:
            raise ValueError("FLRTModelConfig.name must be a non-empty HuggingFace model identifier.")


@dataclass
class FLRTAlgorithmConfig:
    """
    Hyper-parameters of the FLRT optimization loop.

    Attributes:
        n_steps (int): Number of optimization steps per run. Defaults to 500.
        max_runtime_seconds (int): Stop the run after this many seconds even if
            ``n_steps`` has not been reached. ``0`` disables the limit.
            Defaults to 0.
        n_candidates (int): Number of mutated candidates evaluated each step
            (the paper's ``k1``). Defaults to 16.
        n_token_proposals (int): Number of tokens sampled without replacement
            from the victim model's distribution at each position (the paper's
            ``k2``). One of the ``k2`` proposals is then selected uniformly per
            candidate. Defaults to 32.
        buffer_size (int): Size of the optimization buffer that retains the
            top candidate sequences seen so far (Hayase et al., 2024).
            Defaults to 8.
        forcing_probability_threshold (float): Per-token probability at which
            the forcing loss contribution is clamped, so tokens already
            predicted with at least this probability stop attracting optimizer
            effort. Defaults to 0.6.
        fluency_weight (float): Weight ``C_XE`` on the fluency term, the mean
            per-token cross entropy of the prompt under the victim model.
            Defaults to 0.3.
        repetition_weight (float): Weight ``C_rep`` on the repetition penalty.
            The paper suggests ``1.8 * fluency_weight``. Defaults to 0.54.
        repetition_exponent (float): Exponent applied to each token's repeat
            count in the repetition penalty. Defaults to 1.5.
        min_tokens (int): Lower bound on the attack's token length; deletions
            and swaps are disabled at or below it. Defaults to 1.
        max_tokens (int): Upper bound on the attack's token length; insertions
            are disabled at or above it. Defaults to 150.
        early_update_probs (tuple[float, float, float, float]): Mutation
            probabilities ``(delete, insert, swap, edge)`` used before
            ``schedule_step``.
        late_update_probs (tuple[float, float, float, float]): Mutation
            probabilities used from ``schedule_step`` onward.
        schedule_step (int): Optimization step at which the schedule switches
            from ``early_update_probs`` to ``late_update_probs``. Defaults to
            500.
        initial_attack (str): Initial attack string the optimization starts
            from. The paper finds that empty or single-token initializations
            preserve global fluency better than long random strings. Defaults
            to ``""``.
        random_seed (int): Seed for ``torch`` and ``random``. Defaults to 42.
    """

    n_steps: int = 500
    max_runtime_seconds: int = 0
    n_candidates: int = 16
    n_token_proposals: int = 32
    buffer_size: int = 8
    # Forcing length envelopes (n_force_tokens) and teacher matching return with
    # the distillation follow-up.
    forcing_probability_threshold: float = 0.6
    fluency_weight: float = 0.3
    repetition_weight: float = 0.54
    repetition_exponent: float = 1.5
    min_tokens: int = 1
    max_tokens: int = 150
    early_update_probs: tuple[float, float, float, float] = _DEFAULT_EARLY_UPDATE_PROBS
    late_update_probs: tuple[float, float, float, float] = _DEFAULT_LATE_UPDATE_PROBS
    schedule_step: int = 500
    initial_attack: str = ""
    random_seed: int = 42

    def __post_init__(self) -> None:
        """
        Validate optimization hyperparameters.

        Raises:
            ValueError: If a hyperparameter is invalid.
        """
        if self.n_steps <= 0:
            raise ValueError(f"FLRTAlgorithmConfig.n_steps must be > 0, got {self.n_steps}.")
        if self.max_runtime_seconds < 0:
            raise ValueError(f"FLRTAlgorithmConfig.max_runtime_seconds must be >= 0, got {self.max_runtime_seconds}.")
        if self.n_candidates <= 0:
            raise ValueError(f"FLRTAlgorithmConfig.n_candidates must be > 0, got {self.n_candidates}.")
        if self.n_token_proposals <= 0:
            raise ValueError(f"FLRTAlgorithmConfig.n_token_proposals must be > 0, got {self.n_token_proposals}.")
        if self.buffer_size <= 0:
            raise ValueError(f"FLRTAlgorithmConfig.buffer_size must be > 0, got {self.buffer_size}.")
        if not 0.0 < self.forcing_probability_threshold < 1.0:
            raise ValueError(
                "FLRTAlgorithmConfig.forcing_probability_threshold must be in (0, 1), "
                f"got {self.forcing_probability_threshold}."
            )
        if self.fluency_weight < 0 or self.repetition_weight < 0:
            raise ValueError(
                "FLRTAlgorithmConfig fluency_weight and repetition_weight must be >= 0, "
                f"got fluency_weight={self.fluency_weight}, repetition_weight={self.repetition_weight}."
            )
        if self.repetition_exponent < 0:
            raise ValueError(f"FLRTAlgorithmConfig.repetition_exponent must be >= 0, got {self.repetition_exponent}.")
        if self.min_tokens < 0:
            raise ValueError(f"FLRTAlgorithmConfig.min_tokens must be >= 0, got {self.min_tokens}.")
        if self.max_tokens <= self.min_tokens:
            raise ValueError(
                "FLRTAlgorithmConfig.max_tokens must be greater than min_tokens, "
                f"got min_tokens={self.min_tokens}, max_tokens={self.max_tokens}."
            )
        if self.schedule_step < 0:
            raise ValueError(f"FLRTAlgorithmConfig.schedule_step must be >= 0, got {self.schedule_step}.")
        for field_name, probs in (
            ("early_update_probs", self.early_update_probs),
            ("late_update_probs", self.late_update_probs),
        ):
            if len(probs) != 4 or any(p < 0 for p in probs) or sum(probs) <= 0:
                raise ValueError(
                    f"FLRTAlgorithmConfig.{field_name} must be four non-negative "
                    f"probabilities with positive sum (delete, insert, swap, edge), got {probs}."
                )


@dataclass
class FLRTOutputConfig:
    """
    Where the run writes its log/result artefacts.

    Attributes:
        result_prefix (str): Prefix for the per-run JSON log file. The actual
            filename is ``{result_prefix}_{YYYYMMDD-HHMMSS}_{id}.json`` where
            ``id`` is a random 8-character hex string that keeps concurrent runs
            sharing a prefix from writing to the same file. Empty string means
            write the log into the current working directory with no prefix.
        logfile (str): Optional pre-resolved log file path. When set this takes
            precedence over ``result_prefix``.
        verbose (bool): Verbose progress logging during the run. Defaults to True.
    """

    result_prefix: str = ""
    logfile: str = ""
    verbose: bool = True


__all__ = [
    "FLRTAlgorithmConfig",
    "FLRTModelConfig",
    "FLRTOutputConfig",
]
