# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for :mod:`pyrit.executor.promptgen.gcg.extension_protocols`.

These are typing-surface tests: they verify the four ``Protocol``s are
exposed, are ``runtime_checkable``, and accept a minimal in-test concrete
implementation. They do not exercise any real GCG attack code; the default
implementations and the wiring of these protocols into ``GCGAlgorithmConfig`` /
``GCGMultiPromptAttack`` land in follow-up PRs.
"""

from typing import Any

import pytest

# The protocol method signatures reference ``torch.Tensor``. The test bodies
# instantiate real tensors when calling the stub implementations, so the whole
# file is skipped on installs that only have the base ``dev`` extra.
torch = pytest.importorskip("torch", reason="GCG extension protocols reference torch.Tensor")

import pyrit.executor.promptgen.gcg as gcg_pkg  # noqa: E402
from pyrit.executor.promptgen.gcg import (  # noqa: E402
    CandidateFilter,
    LossFunction,
    SamplingStrategy,
    SuffixInitializer,
)
from pyrit.executor.promptgen.gcg import extension_protocols as protocols_module  # noqa: E402

PROTOCOL_NAMES = (
    "CandidateFilter",
    "LossFunction",
    "SamplingStrategy",
    "SuffixInitializer",
)


def test_module_exports_exactly_four_protocols() -> None:
    assert set(protocols_module.__all__) == set(PROTOCOL_NAMES)


def test_protocols_are_reexported_from_package_with_identity() -> None:
    for name in PROTOCOL_NAMES:
        package_attr = getattr(gcg_pkg, name)
        module_attr = getattr(protocols_module, name)
        assert package_attr is module_attr, (
            f"{name} re-exported from pyrit.executor.promptgen.gcg must be the same "
            f"object as pyrit.executor.promptgen.gcg.extension_protocols.{name}"
        )


def test_protocols_are_in_package_dunder_all() -> None:
    for name in PROTOCOL_NAMES:
        assert name in gcg_pkg.__all__


@pytest.mark.parametrize("name", PROTOCOL_NAMES)
def test_protocols_are_runtime_checkable(name: str) -> None:
    proto = getattr(protocols_module, name)
    # ``runtime_checkable`` marks the protocol with ``_is_runtime_protocol = True``;
    # ``isinstance(obj, Proto)`` is only legal on runtime-checkable protocols.
    assert getattr(proto, "_is_runtime_protocol", False), (
        f"{name} must be decorated with @runtime_checkable so isinstance() checks work"
    )


class _StubSamplingStrategy:
    def sample_candidates(
        self,
        *,
        gradient: torch.Tensor,
        control_tokens: torch.Tensor,
        batch_size: int,
        top_k: int,
        temperature: float,
        allow_non_ascii: bool,
        non_ascii_tokens: torch.Tensor,
    ) -> torch.Tensor:
        return control_tokens.unsqueeze(0).repeat(batch_size, 1)


class _StubLossFunction:
    def compute_loss(
        self,
        *,
        logits: torch.Tensor,
        token_ids: torch.Tensor,
        target_slice: slice,
        control_slice: slice,
    ) -> torch.Tensor:
        return torch.zeros(logits.shape[0])


class _StubCandidateFilter:
    def filter_candidates(
        self,
        *,
        candidate_tokens: torch.Tensor,
        tokenizer: Any,
        current_control: str,
    ) -> list[str]:
        return ["stub"] * candidate_tokens.shape[0]


class _StubSuffixInitializer:
    def make_initial_suffix(self, *, tokenizer: Any) -> str:
        return "! ! ! !"


def test_sampling_strategy_accepts_minimal_impl() -> None:
    impl = _StubSamplingStrategy()
    assert isinstance(impl, SamplingStrategy)


def test_loss_function_accepts_minimal_impl() -> None:
    impl = _StubLossFunction()
    assert isinstance(impl, LossFunction)


def test_candidate_filter_accepts_minimal_impl() -> None:
    impl = _StubCandidateFilter()
    assert isinstance(impl, CandidateFilter)


def test_suffix_initializer_accepts_minimal_impl() -> None:
    impl = _StubSuffixInitializer()
    assert isinstance(impl, SuffixInitializer)


class _ClassWithoutAnyProtocolMethods:
    """Has none of the protocol methods. Must fail all isinstance checks."""


@pytest.mark.parametrize(
    "proto",
    [SamplingStrategy, LossFunction, CandidateFilter, SuffixInitializer],
)
def test_class_missing_protocol_method_fails_isinstance(proto: type) -> None:
    bare = _ClassWithoutAnyProtocolMethods()
    assert not isinstance(bare, proto), (
        f"A class missing every method must NOT satisfy {proto.__name__}; "
        "if this assertion fires, the protocol signature has drifted to require nothing."
    )


def test_sampling_strategy_stub_returns_expected_shape() -> None:
    impl = _StubSamplingStrategy()
    control_tokens = torch.tensor([1, 2, 3, 4], dtype=torch.long)
    gradient = torch.zeros((4, 100))
    non_ascii_tokens = torch.tensor([], dtype=torch.long)

    out = impl.sample_candidates(
        gradient=gradient,
        control_tokens=control_tokens,
        batch_size=5,
        top_k=8,
        temperature=1.0,
        allow_non_ascii=True,
        non_ascii_tokens=non_ascii_tokens,
    )
    assert out.shape == (5, 4)


def test_loss_function_stub_returns_expected_shape() -> None:
    impl = _StubLossFunction()
    logits = torch.zeros((3, 10, 50))
    token_ids = torch.zeros((3, 10), dtype=torch.long)

    out = impl.compute_loss(
        logits=logits,
        token_ids=token_ids,
        target_slice=slice(5, 8),
        control_slice=slice(2, 5),
    )
    assert out.shape == (3,)


def test_candidate_filter_stub_returns_expected_length() -> None:
    impl = _StubCandidateFilter()
    candidate_tokens = torch.zeros((7, 4), dtype=torch.long)

    out = impl.filter_candidates(
        candidate_tokens=candidate_tokens,
        tokenizer=object(),
        current_control="prev",
    )
    assert isinstance(out, list)
    assert len(out) == 7
    assert all(isinstance(item, str) for item in out)


def test_suffix_initializer_stub_returns_string() -> None:
    impl = _StubSuffixInitializer()
    out = impl.make_initial_suffix(tokenizer=object())
    assert isinstance(out, str)
    assert out
