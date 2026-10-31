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
from pyrit.models import SeedDataset, SeedObjective

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
    assert config.models[0].tokenizer_kwargs == {"use_fast": True}
    assert config.test_models == []
    assert config.algorithm.n_steps == 500
    assert config.algorithm.batch_size == 512
    assert config.algorithm.sampling is None
    assert config.algorithm.loss is None
    assert config.algorithm.candidate_filter is None
    assert config.algorithm.suffix_init is None
    assert config.algorithm.use_prefix_cache is False
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


@pytest.mark.parametrize(
    "kwargs",
    [
        {"train_goals": ["g1", "g2"], "train_targets": ["t1"]},
        {"test_goals": ["g1"], "test_targets": ["t1", "t2"]},
    ],
)
def test_data_mismatched_lengths_raise(kwargs: dict[str, list[str]]) -> None:
    with pytest.raises(ValueError, match="must have equal length"):
        GCGDataConfig(**kwargs)


def test_data_empty_config_is_allowed() -> None:
    """An empty data config is a legal (degenerate) default."""
    config = GCGDataConfig()
    assert config.train_goals == []
    assert config.train_targets == []
    assert config.test_goals == []
    assert config.test_targets == []


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
        algorithm=GCGAlgorithmConfig(
            n_steps=42,
            batch_size=64,
            target_weight=0.5,
            control_weight=0.5,
            use_prefix_cache=True,
        ),
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
    """GCGDataConfig has its own to_json/from_json (it travels separately for AML)."""
    original = GCGDataConfig(
        train_goals=["train goal 1", "train goal 2"],
        train_targets=["Sure, here is 1", "Sure, here is 2"],
        test_goals=["test goal 1"],
        test_targets=["Sure, here is test 1"],
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


def _seed_dataset_with_targets(pairs: list[tuple[str, str]]) -> SeedDataset:
    return SeedDataset(seeds=[SeedObjective(value=goal, metadata={"gcg_target": target}) for goal, target in pairs])


def test_from_seed_dataset_materializes_train_goals_and_targets() -> None:
    dataset = _seed_dataset_with_targets([("goal a", "Sure, a"), ("goal b", "Sure, b")])
    config = GCGDataConfig.from_seed_dataset(dataset)
    assert config.train_goals == ["goal a", "goal b"]
    assert config.train_targets == ["Sure, a", "Sure, b"]
    assert config.test_goals == []
    assert config.test_targets == []


def test_from_seed_dataset_includes_test_dataset() -> None:
    train = _seed_dataset_with_targets([("goal a", "Sure, a")])
    test = _seed_dataset_with_targets([("goal t", "Sure, t")])
    config = GCGDataConfig.from_seed_dataset(train, test_dataset=test)
    assert config.train_goals == ["goal a"]
    assert config.test_goals == ["goal t"]
    assert config.test_targets == ["Sure, t"]


def test_from_seed_dataset_honors_custom_metadata_key() -> None:
    dataset = SeedDataset(seeds=[SeedObjective(value="goal a", metadata={"completion": "Sure, a"})])
    config = GCGDataConfig.from_seed_dataset(dataset, target_metadata_key="completion")
    assert config.train_targets == ["Sure, a"]


def test_from_seed_dataset_missing_target_metadata_raises() -> None:
    dataset = SeedDataset(seeds=[SeedObjective(value="goal a", metadata={"unrelated": "x"})])
    with pytest.raises(ValueError, match="missing metadata"):
        GCGDataConfig.from_seed_dataset(dataset)


def test_from_seed_dataset_round_trips_through_json() -> None:
    dataset = _seed_dataset_with_targets([("goal a", "Sure, a"), ("goal b", "Sure, b")])
    config = GCGDataConfig.from_seed_dataset(dataset)
    assert GCGDataConfig.from_json(config.to_json()) == config


@pytest.mark.parametrize("field_name", ["train_goals", "train_targets", "test_goals", "test_targets"])
@pytest.mark.parametrize("value", ["abc", None, {"0": "goal"}, [""], [" "], [False], [1], [None], [{}]])
def test_data_config_rejects_invalid_lists_and_values(field_name: str, value: Any) -> None:
    fields: dict[str, Any] = {
        "train_goals": ["train goal"],
        "train_targets": ["train target"],
        "test_goals": ["test goal"],
        "test_targets": ["test target"],
    }
    fields[field_name] = value

    with pytest.raises(ValueError, match=field_name):
        GCGDataConfig(**fields)
    with pytest.raises(ValueError, match=field_name):
        GCGDataConfig.from_json(json.dumps(fields))


@pytest.mark.parametrize("payload", ["[]", "null", '"abc"', "42"])
def test_data_config_json_requires_object(payload: str) -> None:
    with pytest.raises(ValueError, match="must be a JSON object"):
        GCGDataConfig.from_json(payload)


def test_data_config_json_rejects_invalid_syntax() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        GCGDataConfig.from_json("{not-json")


def test_data_config_json_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="unknown fields"):
        GCGDataConfig.from_json('{"goals": ["goal"]}')


def test_data_config_json_explains_legacy_migration() -> None:
    with pytest.raises(ValueError, match="Load a SeedDataset"):
        GCGDataConfig.from_json('{"train_data": "goals.csv", "n_train_data": 50}')


@pytest.mark.parametrize("target", ["", " ", False, 1, [], {"wrong": "schema"}])
def test_from_seed_dataset_rejects_invalid_target_metadata(target: object) -> None:
    dataset = SeedDataset(seeds=[SeedObjective(value="goal", metadata={"gcg_target": target})])

    with pytest.raises(ValueError, match="metadata"):
        GCGDataConfig.from_seed_dataset(dataset)


def test_from_seed_dataset_rejects_blank_goal() -> None:
    dataset = SeedDataset(seeds=[SeedObjective(value=" ", metadata={"gcg_target": "target"})])

    with pytest.raises(ValueError, match="Seed goal must be a nonblank string"):
        GCGDataConfig.from_seed_dataset(dataset)
