# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from unittest.mock import MagicMock

import numpy as np
import pytest

attack_manager_mod = pytest.importorskip(
    "pyrit.auxiliary_attacks.gcg.attack.base.attack_manager",
    reason="GCG optional dependencies (torch, mlflow, etc.) not installed",
)
NpEncoder = attack_manager_mod.NpEncoder
get_nonascii_toks = attack_manager_mod.get_nonascii_toks


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
            tok = token_ids[0] if isinstance(token_ids, list) else token_ids
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
            return {3: "a", 4: "b"}.get(token_ids[0] if isinstance(token_ids, list) else token_ids, "")

        mock_tokenizer.decode = decode_fn
        mock_tokenizer.bos_token_id = None
        mock_tokenizer.eos_token_id = None
        mock_tokenizer.pad_token_id = None
        mock_tokenizer.unk_token_id = None

        result = get_nonascii_toks(mock_tokenizer, device="cpu")
        # Only non-printable tokens should be present, no special tokens
        result_list = result.tolist()
        assert 0 not in result_list  # pad was None
