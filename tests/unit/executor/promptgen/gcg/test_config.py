# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for the GCGConfig dataclass family.

The config module is pure stdlib so it works without the gcg extra installed.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from pyrit.executor.promptgen.gcg.config import (
    GCGAlgorithmConfig,
    GCGConfig,
    GCGDataConfig,
    GCGModelConfig,
    GCGOutputConfig,
    GCGStrategyConfig,
)

if TYPE_CHECKING:
    from pathlib import Path

_LLAMA_2 = "meta-llama/Llama-2-7b-chat-hf"


class _SamplingStub:
    def sample_candidates(
        self,
        *,
        gradient: Any,
        control_tokens: Any,
        batch_size: int,
        top_k: int,
        temperature: float,
        allow_non_ascii: bool,
        non_ascii_tokens: Any,
    ) -> Any:
        return control_tokens


class _LossStub:
    def compute_loss(
        self,
        *,
        logits: Any,
        token_ids: Any,
        target_slice: slice,
        control_slice: slice,
    ) -> Any:
        return logits


class _FilterStub:
    def filter_candidates(
        self,
        *,
        candidate_tokens: Any,
        tokenizer: Any,
        current_control: str,
    ) -> list[str]:
        return [current_control]


class _SuffixInitStub:
    def make_initial_suffix(self, *, tokenizer: Any) -> str:
        return "stub suffix"


def _minimal_config() -> GCGConfig:
    return GCGConfig(models=[GCGModelConfig(name=_LLAMA_2)])


def test_minimal_config_constructs_with_defaults() -> None:
    config = _minimal_config()
    assert len(config.models) == 1
    assert config.models[0].name == _LLAMA_2
    assert config.models[0].device == "cuda:0"
    assert config.models[0].model_kwargs == {"low_cpu_mem_usage": True, "use_cache": False}
    assert config.models[0].tokenizer_kwargs == {"use_fast": False}
    assert config.test_models == []
    assert config.algorithm.n_steps == 500
    assert config.algorithm.batch_size == 512
    assert config.algorithm.sampling is None
    assert config.algorithm.loss is None
    assert config.algorithm.candidate_filter is None
    assert config.algorithm.suffix_init is None
    assert config.strategy.transfer is False
    assert config.output.verbose is True
    assert config.hf_token is None


def test_default_factories_are_independent() -> None:
    a = GCGModelConfig(name=_LLAMA_2)
    b = GCGModelConfig(name=_LLAMA_2)
    a.model_kwargs["low_cpu_mem_usage"] = False
    assert b.model_kwargs["low_cpu_mem_usage"] is True


def test_empty_models_list_raises() -> None:
    with pytest.raises(ValueError, match="GCGConfig.models must contain at least one"):
        GCGConfig(models=[])


def test_empty_model_name_raises() -> None:
    with pytest.raises(ValueError, match="GCGModelConfig.name must be a non-empty"):
        GCGModelConfig(name="")


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("n_steps", 0),
        ("n_steps", -1),
        ("test_steps", 0),
        ("batch_size", 0),
        ("topk", 0),
    ],
)
def test_algorithm_positive_int_validators(field_name: str, value: int) -> None:
    with pytest.raises(ValueError, match=f"GCGAlgorithmConfig.{field_name} must be > 0"):
        GCGAlgorithmConfig(**{field_name: value})


def test_algorithm_negative_weight_raises() -> None:
    with pytest.raises(ValueError, match="must be >= 0"):
        GCGAlgorithmConfig(target_weight=-0.1)


def test_algorithm_both_weights_zero_raises() -> None:
    with pytest.raises(ValueError, match="at least one of target_weight or control_weight"):
        GCGAlgorithmConfig(target_weight=0.0, control_weight=0.0)


def test_algorithm_control_only_is_allowed() -> None:
    config = GCGAlgorithmConfig(target_weight=0.0, control_weight=1.0)
    assert config.target_weight == 0.0
    assert config.control_weight == 1.0


def test_algorithm_empty_control_init_raises() -> None:
    with pytest.raises(ValueError, match="control_init must be a non-empty"):
        GCGAlgorithmConfig(control_init="")


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("sampling", object()),
        ("loss", object()),
        ("candidate_filter", object()),
        ("suffix_init", object()),
    ],
)
def test_algorithm_extension_type_validation(field_name: str, value: object) -> None:
    with pytest.raises(ValueError, match=rf"GCGAlgorithmConfig\.{field_name} must satisfy"):
        GCGAlgorithmConfig(**{field_name: value})


def test_algorithm_accepts_protocol_implementations() -> None:
    config = GCGAlgorithmConfig(
        sampling=_SamplingStub(),
        loss=_LossStub(),
        candidate_filter=_FilterStub(),
        suffix_init=_SuffixInitStub(),
    )
    assert config.sampling is not None
    assert config.loss is not None
    assert config.candidate_filter is not None
    assert config.suffix_init is not None


@pytest.mark.parametrize("field_name", ["n_train_data", "n_test_data"])
def test_data_negative_count_raises(field_name: str) -> None:
    with pytest.raises(ValueError, match=f"GCGDataConfig.{field_name} must be >= 0"):
        GCGDataConfig(**{field_name: -1})


def test_data_zero_train_data_is_allowed() -> None:
    """Zero training rows is a degenerate but legal configuration we don't
    want validators to reject (some smoke tests rely on it)."""
    config = GCGDataConfig(n_train_data=0)
    assert config.n_train_data == 0


def test_strategy_progressive_without_transfer_raises() -> None:
    with pytest.raises(ValueError, match="progressive_goals/progressive_models require transfer=True"):
        GCGStrategyConfig(transfer=False, progressive_goals=True)


def test_strategy_progressive_with_transfer_ok() -> None:
    config = GCGStrategyConfig(transfer=True, progressive_goals=True, progressive_models=True)
    assert config.transfer is True
    assert config.progressive_goals is True
    assert config.progressive_models is True


def test_to_json_round_trip_preserves_all_fields() -> None:
    original = GCGConfig(
        models=[
            GCGModelConfig(
                name=_LLAMA_2,
                device="cuda:1",
                model_kwargs={"low_cpu_mem_usage": False, "use_cache": True, "torch_dtype": "float16"},
                tokenizer_kwargs={"use_fast": True, "padding_side": "left"},
            ),
            GCGModelConfig(name="mistralai/Mistral-7B-Instruct-v0.2"),
        ],
        test_models=[GCGModelConfig(name="lmsys/vicuna-7b-v1.5")],
        algorithm=GCGAlgorithmConfig(n_steps=42, batch_size=64, target_weight=0.5, control_weight=0.5),
        strategy=GCGStrategyConfig(transfer=True, progressive_goals=True, anneal=True),
        output=GCGOutputConfig(result_prefix="results/run1", verbose=False),
        hf_token="hf_secrettoken",
    )

    restored = GCGConfig.from_json(original.to_json())

    assert restored.models[0].name == original.models[0].name
    assert restored.models[0].device == original.models[0].device
    assert restored.models[0].model_kwargs == original.models[0].model_kwargs
    assert restored.models[0].tokenizer_kwargs == original.models[0].tokenizer_kwargs
    assert len(restored.models) == 2
    assert restored.models[1].name == original.models[1].name
    assert restored.test_models[0].name == original.test_models[0].name
    assert restored.algorithm == original.algorithm
    assert restored.strategy == original.strategy
    assert restored.output == original.output
    assert restored.hf_token == original.hf_token


def test_data_config_json_round_trip(tmp_path: Path) -> None:
    """GCGDataConfig now has its own to_json/from_json (it travels separately for AML)."""
    original = GCGDataConfig(
        train_data="https://example/train.csv",
        test_data="https://example/test.csv",
        n_train_data=10,
        n_test_data=5,
    )
    restored = GCGDataConfig.from_json(original.to_json())
    assert restored == original

    target = tmp_path / "data.json"
    original.to_json_file(target)
    assert GCGDataConfig.from_json_file(target) == original


def test_to_json_is_pretty_printed() -> None:
    payload = _minimal_config().to_json()
    assert "\n" in payload
    assert "  " in payload


def test_from_json_invalid_payload_raises() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        GCGConfig.from_json("{not-json")


def test_from_json_missing_models_raises() -> None:
    with pytest.raises(ValueError, match="must contain a 'models' list"):
        GCGConfig.from_json(json.dumps({"data": {}}))


def test_from_json_partial_payload_uses_defaults() -> None:
    payload = json.dumps({"models": [{"name": _LLAMA_2}]})
    restored = GCGConfig.from_json(payload)
    assert restored.algorithm.n_steps == 500
    assert restored.strategy.transfer is False
    assert restored.output.verbose is True


def test_to_json_file_round_trip(tmp_path: Path) -> None:
    config = _minimal_config()
    target = tmp_path / "config.json"
    config.to_json_file(target)
    restored = GCGConfig.from_json_file(target)
    assert restored.models[0].name == _LLAMA_2
