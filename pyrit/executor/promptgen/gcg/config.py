# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Typed configuration objects for the Greedy Coordinate Gradient (GCG) attack.

A minimal call is::

    config = GCGConfig(
        models=[GCGModelConfig(name="meta-llama/Llama-2-7b-chat-hf")],
    )
    await GCG().execute_async(goals=[...], targets=[...])

Each sub-config (``GCGModelConfig``, ``GCGDataConfig``, ``GCGAlgorithmConfig``,
``GCGStrategyConfig``, ``GCGOutputConfig``) ships with defaults that match the
historical YAML configs, so most callers only need to set the model name plus
whichever algorithm hyper-parameters they actually want to override.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from pyrit.executor.promptgen.gcg.extension_protocols import (
        CandidateFilter,
        LossFunction,
        SamplingStrategy,
        SuffixInitializer,
    )

_DEFAULT_CONTROL_INIT: str = "! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! ! !"


def _default_model_kwargs() -> dict[str, Any]:
    return {"low_cpu_mem_usage": True, "use_cache": False}


def _default_tokenizer_kwargs() -> dict[str, Any]:
    return {"use_fast": False}


@dataclass
class GCGModelConfig:
    """
    Identity and loading options for a single HuggingFace model used by GCG.

    Attributes:
        name (str): HuggingFace model identifier such as
            ``"meta-llama/Llama-2-7b-chat-hf"``. The same string is used to load
            both the model weights and the tokenizer.
        device (str): Torch device string for this model. Defaults to ``"cuda:0"``.
        model_kwargs (dict[str, Any]): Extra keyword arguments forwarded to
            ``AutoModelForCausalLM.from_pretrained``. Defaults to
            ``{"low_cpu_mem_usage": True, "use_cache": False}``.
        tokenizer_kwargs (dict[str, Any]): Extra keyword arguments forwarded to
            ``AutoTokenizer.from_pretrained``. Defaults to ``{"use_fast": False}``
            because several legacy chat tokenizers (Llama-2, Vicuna) ship slow
            tokenizers only.
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
            raise ValueError("GCGModelConfig.name must be a non-empty HuggingFace model identifier.")


@dataclass
class GCGDataConfig:
    """
    Goal/target dataset configuration for the GCG attack.

    Used as a typed bundle for AML transport (a job ships its data config as
    a separate JSON file alongside the strategy ``GCGConfig``). Library
    callers loading goals/targets from a CSV can construct one and pass it to
    ``pyrit.executor.promptgen.gcg.data.load_goals_and_targets``.

    Attributes:
        train_data (str): URL or filesystem path to the training-data CSV. Empty
            string falls back to the default Anthropic harmful-behaviors split
            inside ``get_goals_and_targets``.
        test_data (str): URL or filesystem path to the held-out CSV. Empty string
            disables held-out evaluation.
        n_train_data (int): Number of training rows to use. Defaults to 50.
        n_test_data (int): Number of held-out rows to use. Defaults to 0.
    """

    train_data: str = ""
    test_data: str = ""
    n_train_data: int = 50
    n_test_data: int = 0

    def __post_init__(self) -> None:
        """
        Validate requested dataset row counts.

        Raises:
            ValueError: If either requested row count is negative.
        """
        if self.n_train_data < 0:
            raise ValueError(f"GCGDataConfig.n_train_data must be >= 0, got {self.n_train_data}.")
        if self.n_test_data < 0:
            raise ValueError(f"GCGDataConfig.n_test_data must be >= 0, got {self.n_test_data}.")

    def to_json(self) -> str:
        """
        Serialize this config to a JSON string.

        Returns:
            str: A pretty-printed JSON representation.
        """
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, payload: str) -> GCGDataConfig:
        """
        Deserialize a config previously produced by ``to_json``.

        Returns:
            GCGDataConfig: The deserialized data configuration.

        Raises:
            ValueError: If the payload is not valid JSON.
        """
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as e:
            raise ValueError(f"GCGDataConfig.from_json: payload is not valid JSON: {e}") from e
        return cls(**data)

    @classmethod
    def from_json_file(cls, path: str | Path) -> GCGDataConfig:
        """
        Load a config from a JSON file.

        Returns:
            GCGDataConfig: The deserialized data configuration.
        """
        with open(path) as f:
            return cls.from_json(f.read())

    def to_json_file(self, path: str | Path) -> None:
        """Write this config to a JSON file."""
        with open(path, "w") as f:
            f.write(self.to_json())


@dataclass
class GCGAlgorithmConfig:
    """
    Hyper-parameters of the GCG optimization loop.

    Attributes:
        n_steps (int): Number of optimization steps per goal. Defaults to 500.
        test_steps (int): Number of steps between held-out evaluations.
            Defaults to 50.
        batch_size (int): Number of candidate substitutions evaluated each step.
            Defaults to 512.
        topk (int): Top-k gradient positions considered for substitution.
            Defaults to 256.
        temp (float): Sampling temperature placeholder; the current sampling
            implementation samples uniformly from the top-k. Defaults to 1.0.
        target_weight (float): Weight on the target-string cross-entropy loss.
            Defaults to 1.0.
        control_weight (float): Weight on the control-string cross-entropy loss.
            Defaults to 0.0 (target-only signal).
        learning_rate (float): Learning rate kept for API compatibility with the
            historical config; the GCG step itself does not gradient-descend.
            Defaults to 0.01.
        allow_non_ascii (bool): Allow sampling non-ASCII tokens into the suffix.
            Defaults to False.
        filter_cand (bool): Drop candidates whose token-length changes after
            re-tokenization. Defaults to True.
        random_seed (int): Seed for ``torch``/``numpy``/``random``. Defaults to 42.
        control_init (str): Initial suffix string the optimization starts from.
            Defaults to twenty space-separated ``!`` tokens.
        sampling (SamplingStrategy | None): Optional strategy object that
            samples candidate suffix token sequences from the aggregated
            gradient. ``None`` uses the built-in default implementation.
        loss (LossFunction | None): Optional loss object used to score each
            candidate suffix. ``None`` uses the built-in weighted
            cross-entropy default that preserves legacy behavior.
        candidate_filter (CandidateFilter | None): Optional candidate-filter
            object that decodes/prunes sampled candidate token sequences.
            ``None`` uses the built-in length-preserving filter.
        suffix_init (SuffixInitializer | None): Optional initializer object
            that produces the initial suffix string at attack construction
            time. ``None`` uses ``control_init`` verbatim.
    """

    n_steps: int = 500
    test_steps: int = 50
    batch_size: int = 512
    topk: int = 256
    temp: float = 1.0
    target_weight: float = 1.0
    control_weight: float = 0.0
    learning_rate: float = 0.01
    allow_non_ascii: bool = False
    filter_cand: bool = True
    random_seed: int = 42
    control_init: str = _DEFAULT_CONTROL_INIT
    sampling: SamplingStrategy | None = None
    loss: LossFunction | None = None
    candidate_filter: CandidateFilter | None = None
    suffix_init: SuffixInitializer | None = None

    def __post_init__(self) -> None:
        """
        Validate optimization hyperparameters and extension implementations.

        Raises:
            ValueError: If a hyperparameter or extension is invalid.
        """
        if self.n_steps <= 0:
            raise ValueError(f"GCGAlgorithmConfig.n_steps must be > 0, got {self.n_steps}.")
        if self.test_steps <= 0:
            raise ValueError(f"GCGAlgorithmConfig.test_steps must be > 0, got {self.test_steps}.")
        if self.batch_size <= 0:
            raise ValueError(f"GCGAlgorithmConfig.batch_size must be > 0, got {self.batch_size}.")
        if self.topk <= 0:
            raise ValueError(f"GCGAlgorithmConfig.topk must be > 0, got {self.topk}.")
        if self.target_weight < 0 or self.control_weight < 0:
            raise ValueError(
                "GCGAlgorithmConfig target_weight and control_weight must be >= 0, "
                f"got target_weight={self.target_weight}, control_weight={self.control_weight}."
            )
        if self.target_weight == 0 and self.control_weight == 0:
            raise ValueError(
                "GCGAlgorithmConfig requires at least one of target_weight or control_weight to be > 0; "
                "with both at 0 the optimization receives no signal."
            )
        if not self.control_init:
            raise ValueError("GCGAlgorithmConfig.control_init must be a non-empty string.")
        self._validate_extensions()

    def _validate_extensions(self) -> None:
        from pyrit.executor.promptgen.gcg.extension_protocols import (
            CandidateFilter,
            LossFunction,
            SamplingStrategy,
            SuffixInitializer,
        )

        checks = (
            ("sampling", self.sampling, SamplingStrategy),
            ("loss", self.loss, LossFunction),
            ("candidate_filter", self.candidate_filter, CandidateFilter),
            ("suffix_init", self.suffix_init, SuffixInitializer),
        )
        for field_name, value, protocol in checks:
            if value is not None and not isinstance(value, protocol):
                raise ValueError(
                    f"GCGAlgorithmConfig.{field_name} must satisfy {protocol.__name__}, got {type(value)!r}."
                )


@dataclass
class GCGStrategyConfig:
    """
    High-level strategy flags that pick which attack class is used.

    Attributes:
        transfer (bool): If True, run a ``ProgressiveMultiPromptAttack`` (the
            transfer-attack variant); otherwise run an ``IndividualPromptAttack``.
            Defaults to False.
        progressive_goals (bool): Progressively add new goals during a transfer
            attack. Only meaningful with ``transfer=True``. Defaults to False.
        progressive_models (bool): Progressively add new models during a transfer
            attack. Only meaningful with ``transfer=True``. Defaults to False.
        anneal (bool): Use simulated annealing for candidate acceptance.
            Defaults to False.
        incr_control (bool): Incrementally increase the control weight over the
            course of the run. Defaults to False.
        stop_on_success (bool): Terminate as soon as the first goal succeeds.
            Defaults to False.
    """

    transfer: bool = False
    progressive_goals: bool = False
    progressive_models: bool = False
    anneal: bool = False
    incr_control: bool = False
    stop_on_success: bool = False

    def __post_init__(self) -> None:
        """
        Validate progressive strategy dependencies.

        Raises:
            ValueError: If progressive behavior is enabled without transfer.
        """
        if not self.transfer and (self.progressive_goals or self.progressive_models):
            raise ValueError("GCGStrategyConfig.progressive_goals/progressive_models require transfer=True.")


@dataclass
class GCGOutputConfig:
    """
    Where the run writes its log/result artefacts.

    Attributes:
        result_prefix (str): Prefix for the per-run JSON log file. The actual
            filename is ``{result_prefix}_{YYYYMMDD-HHMMSS}.json``. Empty string
            means write the log into the current working directory with no
            prefix (``_<timestamp>.json``); that is rarely what you want.
        logfile (str): Optional pre-resolved log file path. When set this takes
            precedence over ``result_prefix`` for the legacy code paths.
        verbose (bool): Verbose progress logging during the run. Defaults to True.
    """

    result_prefix: str = ""
    logfile: str = ""
    verbose: bool = True


@dataclass
class GCGConfig:
    """
    Top-level strategy configuration for one GCG attack run.

    Bundles everything ``pyrit.executor.promptgen.gcg.GCGGenerator``'s
    constructor needs. Per-execution data (goals, targets) is **not** here —
    those flow through ``GCGGenerator.execute_async``, and for AML transport
    they ride alongside this object as a separate ``GCGDataConfig`` JSON.

    Attributes:
        models (list[GCGModelConfig]): Training models the attack optimizes
            against. Must be non-empty. For the standard single-model attack
            this is a one-element list; transfer attacks pass several.
        test_models (list[GCGModelConfig]): Held-out models used for evaluation
            only. Defaults to an empty list.
        algorithm (GCGAlgorithmConfig): Optimization hyper-parameters. Defaults
            to ``GCGAlgorithmConfig()``.
        strategy (GCGStrategyConfig): High-level strategy flags. Defaults to
            ``GCGStrategyConfig()``.
        output (GCGOutputConfig): Log/result file locations. Defaults to
            ``GCGOutputConfig()``.
        hf_token (str | None): HuggingFace authentication token used when loading
            gated models. ``None`` falls back to the ``HUGGINGFACE_TOKEN``
            environment variable. Defaults to ``None``.
    """

    models: list[GCGModelConfig]
    test_models: list[GCGModelConfig] = field(default_factory=list)
    algorithm: GCGAlgorithmConfig = field(default_factory=GCGAlgorithmConfig)
    strategy: GCGStrategyConfig = field(default_factory=GCGStrategyConfig)
    output: GCGOutputConfig = field(default_factory=GCGOutputConfig)
    hf_token: str | None = None

    def __post_init__(self) -> None:
        """
        Validate that at least one model is configured.

        Raises:
            ValueError: If no model is configured.
        """
        if not self.models:
            raise ValueError("GCGConfig.models must contain at least one GCGModelConfig.")

    def to_json(self) -> str:
        """
        Serialize this config to a JSON string.

        Used by the AzureML transport: the notebook builds a ``GCGConfig`` locally,
        serializes it into the AML job's inputs, and ``experiments/run.py``
        deserializes the same object inside the job.

        Returns:
            str: A pretty-printed JSON document representing the config.
        """
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, payload: str) -> GCGConfig:
        """
        Deserialize a config previously produced by ``to_json``.

        Args:
            payload (str): JSON document matching the shape produced by
                ``to_json``.

        Returns:
            GCGConfig: A new ``GCGConfig`` reconstructed from ``payload``.

        Raises:
            ValueError: If ``payload`` is not valid JSON or is missing
                ``models``.
        """
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as e:
            raise ValueError(f"GCGConfig.from_json: payload is not valid JSON: {e}") from e
        return cls._from_dict(data)

    @classmethod
    def from_json_file(cls, path: str | Path) -> GCGConfig:
        """
        Load a config from a JSON file produced by ``to_json_file``.

        Args:
            path (str | Path): Filesystem path to a JSON config file.

        Returns:
            GCGConfig: A new ``GCGConfig`` reconstructed from the file.
        """
        with open(path) as f:
            return cls.from_json(f.read())

    def to_json_file(self, path: str | Path) -> None:
        """
        Write this config to a JSON file.

        Args:
            path (str | Path): Filesystem path to write to.
        """
        with open(path, "w") as f:
            f.write(self.to_json())

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> GCGConfig:
        if "models" not in data or not isinstance(data["models"], list):
            raise ValueError("GCGConfig payload must contain a 'models' list.")
        return cls(
            models=[GCGModelConfig(**m) for m in data["models"]],
            test_models=[GCGModelConfig(**m) for m in data.get("test_models", [])],
            algorithm=GCGAlgorithmConfig(**data.get("algorithm", {})),
            strategy=GCGStrategyConfig(**data.get("strategy", {})),
            output=GCGOutputConfig(**data.get("output", {})),
            hf_token=data.get("hf_token"),
        )


__all__ = [
    "GCGAlgorithmConfig",
    "GCGConfig",
    "GCGDataConfig",
    "GCGModelConfig",
    "GCGOutputConfig",
    "GCGStrategyConfig",
]
