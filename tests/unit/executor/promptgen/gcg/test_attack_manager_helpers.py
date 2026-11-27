# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

attack_manager_mod = pytest.importorskip(
    "pyrit.executor.promptgen.gcg.attack.base.attack_manager",
    reason="GCG optional dependencies (torch, mlflow, etc.) not installed",
)
NpEncoder = attack_manager_mod.NpEncoder
get_nonascii_toks = attack_manager_mod.get_nonascii_toks
get_unsampleable_toks = attack_manager_mod.get_unsampleable_toks
clamp_unsampleable_toks = attack_manager_mod.clamp_unsampleable_toks
torch = attack_manager_mod.torch


class TestNpEncoder:
    """Tests for the NpEncoder JSON encoder class."""

    def test_encodes_numpy_integer(self) -> None:
        """NpEncoder should convert numpy integers to Python ints."""
        result = json.dumps({"val": np.int64(42)}, cls=NpEncoder)
        assert json.loads(result) == {"val": 42}

    def test_encodes_numpy_floating(self) -> None:
        """NpEncoder should convert numpy floats to Python floats."""
        result = json.dumps({"val": np.float32(3.14)}, cls=NpEncoder)
        parsed = json.loads(result)
        assert abs(parsed["val"] - 3.14) < 0.01

    def test_encodes_numpy_ndarray(self) -> None:
        """NpEncoder should convert numpy arrays to Python lists."""
        arr = np.array([1, 2, 3])
        result = json.dumps({"val": arr}, cls=NpEncoder)
        assert json.loads(result) == {"val": [1, 2, 3]}

    def test_encodes_regular_types(self) -> None:
        """NpEncoder should pass through regular JSON-serializable types."""
        result = json.dumps({"str": "hello", "int": 5, "float": 1.5}, cls=NpEncoder)
        assert json.loads(result) == {"str": "hello", "int": 5, "float": 1.5}

    def test_raises_on_non_serializable(self) -> None:
        """NpEncoder should raise TypeError for non-serializable types."""
        with pytest.raises(TypeError):
            json.dumps({"val": object()}, cls=NpEncoder)


class TestGetNonasciiToks:
    """Tests for the get_nonascii_toks function."""

    def test_returns_tensor_with_non_ascii_indices(self) -> None:
        """Should return a tensor containing non-ASCII token indices."""
        mock_tokenizer = MagicMock()
        mock_tokenizer.vocab_size = 10
        # Tokens 3-9: make some ascii, some not
        mock_tokenizer.decode.side_effect = lambda ids: {
            3: "a",
            4: "b",
            5: "\xff",  # non-ascii
            6: "c",
            7: "\x80",  # non-ascii
            8: "d",
            9: "e",
        }.get(ids[0] if isinstance(ids, list) else ids, "")

        # Need to handle list input
        def decode_fn(token_ids: list[int]) -> str:
            tok = token_ids[0]
            chars = {3: "a", 4: "b", 5: "\xff", 6: "c", 7: "\x80", 8: "d", 9: "e"}
            return chars.get(tok, "")

        mock_tokenizer.decode = decode_fn
        mock_tokenizer.bos_token_id = 1
        mock_tokenizer.eos_token_id = 2
        mock_tokenizer.pad_token_id = 0
        mock_tokenizer.unk_token_id = None

        result = get_nonascii_toks(mock_tokenizer, device="cpu")

        # Should contain non-ascii tokens (5, 7) plus special tokens (1, 2, 0)
        result_set = set(result.tolist())
        assert 5 in result_set  # non-ascii \xff
        assert 7 in result_set  # non-ascii \x80
        assert 1 in result_set  # bos
        assert 2 in result_set  # eos
        assert 0 in result_set  # pad

    def test_skips_none_special_tokens(self) -> None:
        """Should not include special token IDs that are None."""
        mock_tokenizer = MagicMock()
        mock_tokenizer.vocab_size = 5

        def decode_fn(token_ids: list[int]) -> str:
            return {3: "a", 4: "b"}.get(token_ids[0], "")

        mock_tokenizer.decode = decode_fn
        mock_tokenizer.bos_token_id = None
        mock_tokenizer.eos_token_id = None
        mock_tokenizer.pad_token_id = None
        mock_tokenizer.unk_token_id = None

        result = get_nonascii_toks(mock_tokenizer, device="cpu")
        # Only non-printable tokens should be present, no special tokens
        result_list = result.tolist()
        assert 0 not in result_list  # pad was None


def _offline_tokenizer() -> Any:
    """Word-level fast tokenizer: <s> (BOS, special), "!", "a", plus added tokens "<|ctl|>" (special) and "word"."""
    from tokenizers import Tokenizer, models, pre_tokenizers, processors
    from transformers import PreTrainedTokenizerFast

    backend = Tokenizer(models.WordLevel({"<s>": 0, "!": 1, "a": 2}, unk_token="<s>"))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    backend.post_processor = processors.TemplateProcessing(single="<s> $A", special_tokens=[("<s>", 0)])
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, bos_token="<s>")
    tokenizer.add_special_tokens({"additional_special_tokens": ["<|ctl|>"]})  # id 3
    tokenizer.add_tokens(["word"])  # id 4, not special
    return tokenizer


class TestGetUnsampleableToks:
    """Tests for get_unsampleable_toks."""

    def test_masks_special_tokens_and_padding_rows(self) -> None:
        result = get_unsampleable_toks(tokenizer=_offline_tokenizer(), num_embeddings=8)

        # <s> is special; <|ctl|> and the added non-special "word" sit at or above vocab_size (3), like
        # <think> on Qwen checkpoints; rows 5-7 are padding past the tokenizer.
        assert result.tolist() == [0, 3, 4, 5, 6, 7]

    def test_tokenizer_longer_than_embedding_rows(self) -> None:
        # Gemma 3 text checkpoints have one more tokenizer entry than embedding rows.
        result = get_unsampleable_toks(tokenizer=_offline_tokenizer(), num_embeddings=4)

        assert result.tolist() == [0, 3]


class TestClampUnsampleableToks:
    """Tests for clamp_unsampleable_toks."""

    def test_replaces_unsampleable_ids_with_bang_not_bos(self) -> None:
        candidates = torch.tensor([[2, 3, 4, 7]])

        clamp_unsampleable_toks(candidates, tokenizer=_offline_tokenizer())

        # <|ctl|>, the added token "word" and padding row 7 become "!" (id 1), not <s>; "a" (2) is kept.
        assert candidates.tolist() == [[2, 1, 1, 1]]


class TestModelWorkerDtype:
    """ModelWorker loads in float16 unless model_kwargs picks a dtype."""

    @pytest.mark.parametrize(
        ("model_kwargs", "expected"),
        [
            ({}, {"dtype": torch.float16}),
            ({"torch_dtype": torch.bfloat16}, {"torch_dtype": torch.bfloat16}),
            ({"dtype": torch.float32}, {"dtype": torch.float32}),
        ],
    )
    def test_model_kwargs_override_the_dtype(self, model_kwargs: dict, expected: dict) -> None:
        with (
            patch.object(attack_manager_mod.AutoModelForCausalLM, "from_pretrained") as from_pretrained,
            patch.object(attack_manager_mod.mp, "JoinableQueue", side_effect=[MagicMock(), MagicMock()]),
        ):
            attack_manager_mod.ModelWorker(
                model_path="m", token=None, model_kwargs=dict(model_kwargs), tokenizer=MagicMock(), device="cpu"
            )

        kwargs = from_pretrained.call_args.kwargs
        assert {k: kwargs[k] for k in ("torch_dtype", "dtype") if k in kwargs} == expected
