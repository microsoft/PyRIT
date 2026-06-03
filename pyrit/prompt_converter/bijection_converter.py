# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pathlib
import random
import string
from typing import Literal, Optional

from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.models import ComponentIdentifier, PromptDataType, SeedPrompt
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

_TEACHING_SENTENCES = [
    "Hello, how are you today?",
    "The quick brown fox jumps over the lazy dog.",
    "Can you help me with something?",
    "What is the best way to learn programming?",
    "I would like some information about history.",
    "Please tell me about the weather forecast.",
    "How do computers process information?",
    "What are the main features of this product?",
    "I enjoy reading books and learning new things.",
    "Could you explain this concept in simple terms?",
]


class BijectionConverter(PromptConverter):
    """
    Encodes or decodes text using a bijective character mapping.

    **Encode mode** (``direction="encode"``, default): generates a random mapping
    from English lowercase letters to either permuted letters
    (``mapping_type="letter"``) or fixed-length digit strings
    (``mapping_type="digit"``).  When ``append_description=True`` the output is
    wrapped in a teaching preamble (mapping table + ``num_teaching_shots`` benign
    Q&A pairs) that teaches a target model the custom language before the encoded
    query is presented.

    **Decode mode** (``direction="decode"``): inverts the mapping and applies it
    to the input.  Requires ``custom_mapping``.  No teaching preamble is added.
    Use this as a response-side converter so the scorer always receives plaintext.

    The mapping is fixed at instantiation time.  Use ``seed`` for reproducibility,
    or read the ``mapping`` property to retrieve the active mapping.

    Bijection Learning [@liu2024bijectionlearning].
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    _ALPHABET = string.ascii_lowercase

    def __init__(
        self,
        *,
        direction: Literal["encode", "decode"] = "encode",
        mapping_type: Literal["letter", "digit"] = "digit",
        fixed_points: int = 13,
        digit_length: int = 2,
        num_teaching_shots: int = 5,
        seed: Optional[int] = None,
        custom_mapping: Optional[dict[str, str]] = None,
        append_description: bool = True,
    ) -> None:
        """
        Args:
            direction: ``"encode"`` (default) builds the forward mapping and
                optionally prepends the teaching preamble.  ``"decode"`` inverts
                ``custom_mapping`` and applies it to the input — requires
                ``custom_mapping`` and ignores all encode-only parameters.
            mapping_type: ``"letter"`` permutes lowercase letters among
                themselves; ``"digit"`` maps each remapped letter to a
                zero-padded numeric string of length ``digit_length``.
                Encode mode only.
            fixed_points: Number of lowercase letters (0–25) that map to
                themselves.  0 = all 26 letters remapped (maximum complexity).
                26 is rejected because the identity mapping is a silent no-op.
                Encode mode only.
            digit_length: Length of numeric codes for ``mapping_type="digit"``.
                Must be 1–5.  Encode mode only.
            num_teaching_shots: Benign Q&A pairs included in the teaching
                preamble.  Only used in encode mode when
                ``append_description=True``.
            seed: Integer seed for reproducible mapping generation.  ``None``
                produces a fresh random mapping on each instantiation.
                Encode mode only; mutually exclusive with ``custom_mapping``.
            custom_mapping: User-supplied letter→code dict.  Required for
                ``direction="decode"``.  In encode mode, bypasses auto-generation
                and is mutually exclusive with ``seed``, ``fixed_points``, and
                ``digit_length``.
            append_description: When ``True`` (default, encode mode only) the
                converted prompt includes the mapping table and teaching
                examples.  ``False`` returns only the encoded text.

        Raises:
            ValueError: If parameter constraints are violated or mutually
                exclusive arguments are combined.
        """
        self._direction = direction

        if direction == "decode":
            if custom_mapping is None:
                raise ValueError("custom_mapping is required when direction='decode'.")
            self._mapping: dict[str, str] = dict(custom_mapping)
            # Auto-detect digit_length from the mapping values so the caller
            # does not have to pass it separately.
            self._digit_length = next(
                (len(v) for v in custom_mapping.values() if v.isdigit()),
                digit_length,
            )
            # Encode-only parameters are irrelevant in decode mode.
            self._mapping_type = mapping_type
            self._fixed_points = 0
            self._num_teaching_shots = 0
            self._append_description = False
            self._seed = None
            return

        # --- encode mode ---
        if custom_mapping is not None:
            conflicting = {
                "seed": seed is not None,
                "fixed_points": fixed_points != 13,
                "digit_length": digit_length != 2,
            }
            bad = [k for k, v in conflicting.items() if v]
            if bad:
                raise ValueError(f"custom_mapping is mutually exclusive with: {', '.join(bad)}.")
        if not 0 <= fixed_points <= 25:
            raise ValueError(
                "fixed_points must be between 0 and 25 inclusive.  "
                "26 (identity mapping) is rejected because it produces no encoding."
            )
        if not 1 <= digit_length <= 5:
            raise ValueError("digit_length must be between 1 and 5 inclusive.")
        if num_teaching_shots < 0:
            raise ValueError("num_teaching_shots must be non-negative.")

        self._mapping_type = mapping_type
        self._fixed_points = fixed_points
        self._digit_length = digit_length
        self._num_teaching_shots = min(num_teaching_shots, len(_TEACHING_SENTENCES))
        self._append_description = append_description
        self._seed = seed

        if custom_mapping is not None:
            self._mapping = dict(custom_mapping)
        else:
            rng = random.Random(seed)
            self._mapping = self._generate_mapping(rng)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def mapping(self) -> dict[str, str]:
        """The bijection mapping actually used (lowercase letter → encoded token)."""
        return dict(self._mapping)

    @property
    def digit_length(self) -> int:
        """Numeric code length (only meaningful for ``mapping_type='digit'``)."""
        return self._digit_length

    @staticmethod
    def decode(text: str, mapping: dict[str, str], digit_length: int = 2) -> str:
        """
        Decode a bijection-encoded string back to plaintext.

        For letter mappings the inverse dict is applied character-by-character.
        For digit mappings the string is walked left-to-right: only runs of
        exactly ``digit_length`` consecutive digits are looked up as codes; all
        other characters (including fixed-point letters) are passed through
        unchanged.

        Args:
            text: Bijection-encoded string to decode.
            mapping: The forward mapping (letter → code) used during encoding.
            digit_length: Width of numeric codes.  Must match the value used
                when encoding.

        Returns:
            Decoded plaintext string.
        """
        inverse = {v: k for k, v in mapping.items()}
        uses_digit_codes = any(v.isdigit() for v in mapping.values())

        if not uses_digit_codes:
            return "".join(inverse.get(ch, ch) for ch in text)

        result: list[str] = []
        i = 0
        while i < len(text):
            if text[i].isdigit():
                code = text[i : i + digit_length]
                if len(code) == digit_length and code in inverse:
                    result.append(inverse[code])
                    i += digit_length
                else:
                    # Partial run or unknown code — pass the single digit through.
                    result.append(text[i])
                    i += 1
            else:
                result.append(inverse.get(text[i], text[i]))
                i += 1
        return "".join(result)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Encode or decode ``prompt`` depending on ``direction``.

        In encode mode the prompt is transformed through the forward mapping and
        optionally wrapped in the teaching preamble.  In decode mode the inverse
        mapping is applied and no preamble is added.

        Args:
            prompt: Text to transform.
            input_type: Must be ``"text"``.

        Returns:
            ConverterResult with the transformed text.

        Raises:
            ValueError: If ``input_type`` is not ``"text"``.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        if self._direction == "decode":
            return ConverterResult(
                output_text=self.decode(prompt, self._mapping, self._digit_length),
                output_type="text",
            )

        # encode
        encoded = self._encode(prompt)
        if not self._append_description:
            return ConverterResult(output_text=encoded, output_type="text")

        prompt_template = SeedPrompt.from_yaml_file(
            pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "bijection_description.yaml"
        )
        output_text = prompt_template.render_template_value(
            mapping_table=self._format_mapping_table(),
            examples=self._format_teaching_shots(),
            prompt=encoded,
        )
        return ConverterResult(output_text=output_text, output_type="text")

    # ------------------------------------------------------------------
    # Identifier
    # ------------------------------------------------------------------

    def _build_identifier(self) -> ComponentIdentifier:
        mapping_hash = hash(tuple(sorted(self._mapping.items())))
        return self._create_identifier(
            params={
                "direction": self._direction,
                "mapping_type": self._mapping_type,
                "fixed_points": self._fixed_points,
                "digit_length": self._digit_length,
                "num_teaching_shots": self._num_teaching_shots,
                "append_description": self._append_description,
                "mapping_hash": mapping_hash,
            }
        )

    # ------------------------------------------------------------------
    # Internal helpers (encode mode)
    # ------------------------------------------------------------------

    def _generate_mapping(self, rng: random.Random) -> dict[str, str]:
        """
        Build the bijection dict using the configured parameters and ``rng``.

        Returns:
            dict[str, str]: Forward mapping from lowercase letter to encoded token.

        Raises:
            ValueError: If ``digit_length`` is too small to produce enough unique
                codes for the number of letters that need remapping.
        """
        alphabet = list(self._ALPHABET)

        fixed: set[str] = set(rng.sample(alphabet, self._fixed_points))
        remapped = [c for c in alphabet if c not in fixed]

        mapping: dict[str, str] = {c: c for c in fixed}

        if self._mapping_type == "letter":
            permuted = remapped[:]
            for _ in range(200):
                rng.shuffle(permuted)
                if all(permuted[i] != remapped[i] for i in range(len(remapped))):
                    break
            mapping.update(dict(zip(remapped, permuted, strict=False)))

        else:  # "digit"
            max_code = 10**self._digit_length
            if len(remapped) > max_code:
                raise ValueError(
                    f"digit_length={self._digit_length} supports at most {max_code} distinct codes "
                    f"but {len(remapped)} letters need remapping.  Increase digit_length or fixed_points."
                )
            all_codes = [f"{i:0{self._digit_length}d}" for i in range(max_code)]
            chosen = rng.sample(all_codes, len(remapped))
            mapping.update(dict(zip(remapped, chosen, strict=False)))

        return mapping

    def _encode(self, text: str) -> str:
        """
        Apply the forward bijection mapping (only lowercase letters are affected).

        Returns:
            str: The encoded string.
        """
        return "".join(self._mapping.get(ch, ch) for ch in text)

    def _format_mapping_table(self) -> str:
        return str(self._mapping)

    def _format_teaching_shots(self) -> str:
        sentences = _TEACHING_SENTENCES[: self._num_teaching_shots]
        shots = [f"User: {sentence}\nAssistant: {self._encode(sentence.lower())}" for sentence in sentences]
        return "\n\n".join(shots)
