# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for the FLRT configuration objects."""

from __future__ import annotations

import pytest

from pyrit.executor.promptgen.flrt import FLRTAlgorithmConfig, FLRTModelConfig, FLRTOutputConfig


class TestFLRTModelConfig:
    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty HuggingFace model identifier"):
            FLRTModelConfig(name="")

    def test_defaults(self) -> None:
        config = FLRTModelConfig(name="some/model")
        assert config.device == "cuda:0"
        assert config.model_kwargs == {"low_cpu_mem_usage": True, "use_cache": False}
        assert config.tokenizer_kwargs == {"use_fast": True}


class TestFLRTAlgorithmConfig:
    def test_defaults_match_paper(self) -> None:
        config = FLRTAlgorithmConfig()
        assert config.n_candidates == 16
        assert config.n_token_proposals == 32
        assert config.buffer_size == 8
        assert config.forcing_probability_threshold == 0.6
        assert config.repetition_exponent == 1.5
        assert config.repetition_weight == pytest.approx(1.8 * config.fluency_weight, abs=0.01)
        assert sum(config.early_update_probs) == pytest.approx(1.0)
        assert sum(config.late_update_probs) == pytest.approx(1.0)

    @pytest.mark.parametrize(
        "override,match",
        [
            ({"n_steps": 0}, "n_steps must be > 0"),
            ({"max_runtime_seconds": -1}, "max_runtime_seconds must be >= 0"),
            ({"n_candidates": 0}, "n_candidates must be > 0"),
            ({"n_token_proposals": -1}, "n_token_proposals must be > 0"),
            ({"buffer_size": 0}, "buffer_size must be > 0"),
            ({"forcing_probability_threshold": 0.0}, "forcing_probability_threshold must be in"),
            ({"forcing_probability_threshold": 1.0}, "forcing_probability_threshold must be in"),
            ({"fluency_weight": -0.1}, "fluency_weight and repetition_weight must be >= 0"),
            ({"repetition_weight": -0.1}, "fluency_weight and repetition_weight must be >= 0"),
            ({"repetition_exponent": -1.0}, "repetition_exponent must be >= 0"),
            ({"min_tokens": -1}, "min_tokens must be >= 0"),
            ({"min_tokens": 10, "max_tokens": 10}, "max_tokens must be greater than min_tokens"),
            ({"schedule_step": -1}, "schedule_step must be >= 0"),
        ],
    )
    def test_invalid_values_raise(self, override: dict, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            FLRTAlgorithmConfig(**override)

    @pytest.mark.parametrize(
        "probs",
        [
            (0.5, 0.5, 0.5),
            (0.5, 0.5, 0.5, 0.5, 0.5),
            (0.0, 0.0, 0.0, 0.0),
            (-0.5, 0.5, 0.5, 0.5),
        ],
    )
    def test_invalid_update_probs_raise(self, probs: tuple) -> None:
        with pytest.raises(ValueError, match="four non-negative"):
            FLRTAlgorithmConfig(early_update_probs=probs)
        with pytest.raises(ValueError, match="four non-negative"):
            FLRTAlgorithmConfig(late_update_probs=probs)


class TestFLRTOutputConfig:
    def test_defaults(self) -> None:
        config = FLRTOutputConfig()
        assert config.result_prefix == ""
        assert config.logfile == ""
        assert config.verbose is True
