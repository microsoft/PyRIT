# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
import random
import string

from pyrit.models import ComponentIdentifier, PromptDataType
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter


class BijectionConverter(PromptConverter, abc.ABC):
    """
    Abstract base class for bijection converters.
    Converts a prompt using a one-to-one character mapping to bypass safety filters.
    Based on the Bijection Learning attack [@huang2024bijectionlearning].
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(
        self,
        *,
        mapping: dict[str, str] | None = None,
        seed: int | None = None,
    ) -> None:
        """
        Args:
            mapping: Optional explicit mapping dict. If provided, used directly.
            seed: Optional random seed for reproducibility.
        """
        super().__init__()
        rng = random.Random(seed)
        self._mapping = mapping if mapping is not None else self._generate_mapping(rng)
        self._inverse_mapping = {v: k for k, v in self._mapping.items()}

    @abc.abstractmethod
    def _generate_mapping(self, rng: random.Random) -> dict[str, str]:
        """Generate the bijection mapping."""
        ...

    @property
    def mapping(self) -> dict[str, str]:
        """Return the character-to-character mapping used for encoding."""
        return self._mapping

    @property
    def inverse_mapping(self) -> dict[str, str]:
        """Return the inverse mapping used for decoding."""
        return self._inverse_mapping

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={
                "mapping": str(self._mapping),
            },
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Encode the prompt using the bijection mapping.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the encoded prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        encoded = ""
        for char in prompt:
            if char.lower() in self._mapping:
                if char.isupper():
                    encoded += self._mapping[char.lower()].upper()
                else:
                    encoded += self._mapping[char]
            else:
                encoded += char

        return ConverterResult(output_text=encoded, output_type="text")

    def decode(self, encoded_text: str) -> str:
        """
        Decode an encoded response back to plain text using the inverse mapping.

        Args:
            encoded_text (str): The encoded text to decode.

        Returns:
            str: The decoded plain text.
        """
        decoded = ""
        encoded_tokens = sorted(self._inverse_mapping, key=len, reverse=True)
        i = 0
        while i < len(encoded_text):
            matched = False
            for encoded_token in encoded_tokens:
                candidate = encoded_text[i : i + len(encoded_token)]
                if candidate == encoded_token:
                    decoded += self._inverse_mapping[encoded_token]
                    i += len(encoded_token)
                    matched = True
                    break
                if len(encoded_token) == 1 and candidate.lower() == encoded_token and candidate.isupper():
                    decoded += self._inverse_mapping[encoded_token].upper()
                    i += 1
                    matched = True
                    break

            if not matched:
                decoded += encoded_text[i]
                i += 1

        return decoded


class LetterBijectionConverter(BijectionConverter):
    """
    Bijection converter that maps letters to other letters.
    """

    def __init__(
        self,
        *,
        fixed_size: int = 0,
        mapping: dict[str, str] | None = None,
        seed: int | None = None,
    ) -> None:
        """
        Initialize the converter.

        Args:
            fixed_size (int): Number of leading letters to keep unchanged (identity mapping).
            mapping (dict[str, str], Optional): Explicit mapping dict. If provided, used directly.
            seed (int, Optional): Random seed for reproducibility.
        """
        self._fixed_size = fixed_size
        super().__init__(mapping=mapping, seed=seed)

    @property
    def fixed_size(self) -> int:
        """Return the number of leading letters kept unchanged."""
        return self._fixed_size

    def _generate_mapping(self, rng: random.Random) -> dict[str, str]:
        letters = list(string.ascii_lowercase)
        fixed_letters = letters[: self._fixed_size]
        letters_to_shuffle = letters[self._fixed_size :]
        shuffled = letters_to_shuffle.copy()
        rng.shuffle(shuffled)

        mapping = {letter: letter for letter in fixed_letters}
        mapping.update(zip(letters_to_shuffle, shuffled, strict=True))
        return mapping

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={
                "fixed_size": self._fixed_size,
                "mapping": str(self._mapping),
            },
        )


class DigitBijectionConverter(BijectionConverter):
    """
    Bijection converter that maps letters to random digit strings.
    Each English letter maps to a randomly-selected num_digits-digit number.
    Based on mode 2 from the Bijection Learning attack [@huang2024bijectionlearning].
    """

    def __init__(
        self,
        *,
        num_digits: int = 2,
        mapping: dict[str, str] | None = None,
        seed: int | None = None,
    ) -> None:
        """
        Initialize the converter.

        Args:
            num_digits (int): Length of digit strings to map letters to (2-4).
            mapping (dict[str, str], Optional): Explicit mapping dict. If provided, used directly.
            seed (int, Optional): Random seed for reproducibility.

        Raises:
            ValueError: If num_digits is not between 2 and 4.
        """
        if not 2 <= num_digits <= 4:
            raise ValueError("num_digits must be between 2 and 4")
        self._num_digits = num_digits
        super().__init__(mapping=mapping, seed=seed)

    @property
    def num_digits(self) -> int:
        """Return the length of digit strings letters are mapped to."""
        return self._num_digits

    def _generate_mapping(self, rng: random.Random) -> dict[str, str]:
        letters = list(string.ascii_lowercase)
        low = 10 ** (self._num_digits - 1)
        high = 10**self._num_digits
        values = rng.sample(range(low, high), len(letters))
        return {letter: str(v) for letter, v in zip(letters, values, strict=True)}

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={
                "num_digits": self._num_digits,
                "mapping": str(self._mapping),
            },
        )

class TokenBijectionConverter(BijectionConverter):
    """
    Bijection converter that maps letters to tokens from a tokenizer vocabulary.
    Each English letter maps to a randomly sampled distinct token from the
    target model's tokenizer vocabulary.
    Based on mode 3 from arXiv:2410.01294 (Haize Labs).
    """

    def __init__(
        self,
        *,
        tokenizer: object,
        mapping: dict[str, str] | None = None,
        seed: int | None = None,
    ) -> None:
        """
        Args:
            tokenizer: A HuggingFace tokenizer with get_vocab() method.
            mapping: Optional explicit mapping dict.
            seed: Optional random seed for reproducibility.
        """
        self._tokenizer = tokenizer
        super().__init__(mapping=mapping, seed=seed)

    def _generate_mapping(self, rng: random.Random) -> dict[str, str]:
        vocab = self._tokenizer.get_vocab()

        candidates = [
            token for token in vocab.keys()
            if (
                len(token) >= 3
                and len(token) <= 10
                and token.isalpha()
                and not token.startswith("<")
                and not token.startswith("##")
                and token.lower() == token
            )
        ]

        if len(candidates) < 26:
            raise ValueError(
                f"Not enough valid tokens in vocabulary. Found {len(candidates)}, need 26."
            )

        selected = rng.sample(candidates, 26)
        letters = list(string.ascii_lowercase)
        return {letter: token for letter, token in zip(letters, selected)}

    def _build_identifier(self) -> dict:
        return self._create_identifier(params={
            "mapping": str(self._mapping),
        })