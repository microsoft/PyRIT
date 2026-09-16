# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import re
from typing import Literal

from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType

PinyinMode = Literal["full", "initial", "mixed"]

# Han (Chinese) character ranges eligible for conversion. Other characters
# (Latin, digits, punctuation, whitespace, emoji, ...) are passed through untouched.
_HAN_PATTERN = re.compile(
    "["
    "\u3007"  # Ideographic number zero
    "㐀-䶿"  # CJK Unified Ideographs Extension A
    "一-鿿"  # CJK Unified Ideographs
    "豈-﫿"  # CJK Compatibility Ideographs
    "\U00020000-\U0002a6df"  # CJK Unified Ideographs Extension B
    "\U0002a700-\U0002ee5f"  # CJK Unified Ideographs Extensions C-F and I
    "\U0002f800-\U0002fa1f"  # CJK Compatibility Ideographs Supplement
    "\U00030000-\U0003347f"  # CJK Unified Ideographs Extensions G, H, and J
    "]"
)


class PinyinConverter(Converter):
    """
    Replaces Chinese (Hanzi) characters with their Pinyin romanization.

    Pinyin mixing is a Chinese-specific adversarial text transformation: Hanzi characters
    or spans are rewritten as full or abbreviated Pinyin while the text stays understandable
    to a Chinese-reading model. This can bypass keyword- and token-level safety filters that
    match on Hanzi rather than on romanized readings. The pattern is described in recent work
    on Chinese LLM safety such as CSSBench.

    The converter uses no LLM call. It resolves readings using phrase context before
    replacing individual characters:

    - ``full``: each selected Hanzi becomes its full Pinyin reading without tone marks
      (e.g. ``中`` -> ``zhong``).
    - ``initial``: each selected Hanzi becomes the first letter of its reading
      (e.g. ``中`` -> ``z``).
    - ``mixed``: each selected Hanzi is independently rendered as either its full reading or
      its initial.

    ``proportion`` controls how many of the Hanzi are converted; a value below ``1.0`` leaves
    the rest as Hanzi, producing mixed Hanzi/Pinyin text. Characters that are not Hanzi are
    always left unchanged, as are Hanzi without a dictionary reading. Pass ``seed`` for
    reproducible selection.

    Pinyin dictionaries are loaded lazily, and phrase lookup runs in a worker thread.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(
        self,
        *,
        mode: PinyinMode = "full",
        proportion: float = 1.0,
        separator: str = "",
        seed: int | None = None,
    ) -> None:
        """
        Initialize the converter.

        Args:
            mode (PinyinMode): How a converted Hanzi is rendered. ``"full"`` uses the full
                toneless reading, ``"initial"`` uses only the first letter, and ``"mixed"``
                chooses one of the two independently per character. Defaults to ``"full"``.
            proportion (float): Fraction in ``[0.0, 1.0]`` of the Hanzi characters to convert.
                The selected count is ``round(proportion * number_of_hanzi)`` and the positions
                are chosen at random (seedable). ``1.0`` converts every Hanzi. Defaults to
                ``1.0``.
            separator (str): String inserted after each converted syllable, except at the end
                of the prompt. Original characters, including trailing whitespace, are preserved.
                Full Pinyin spans run together by default (``中心`` -> ``zhongxin``); pass
                ``separator=" "`` to keep syllable boundaries readable (``zhong xin``).
                Defaults to ``""``.
            seed (int | None): Optional seed for reproducible selection and, in ``"mixed"``
                mode, reproducible per-character rendering. Defaults to None.

        Raises:
            ValueError: If ``mode`` is not one of ``"full"``, ``"initial"``, ``"mixed"`` or if
                ``proportion`` is outside ``[0.0, 1.0]``.
        """
        if mode not in ("full", "initial", "mixed"):
            raise ValueError('mode must be one of "full", "initial", or "mixed"')
        if not 0.0 <= proportion <= 1.0:
            raise ValueError("proportion must be between 0.0 and 1.0")

        self._mode: PinyinMode = mode
        self._proportion = proportion
        self._separator = separator
        self._seed = seed

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with Pinyin parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "mode": self._mode,
                "proportion": self._proportion,
                "separator": self._separator,
                "seed": self._seed,
            }
        )

    def _get_pinyin_readings(self, prompt: str) -> list[str]:
        """
        Resolve phrase-aware Pinyin readings aligned with the original characters.

        Args:
            prompt (str): The complete prompt used as pronunciation context.

        Returns:
            list[str]: One reading per character, preserving characters without a reading.
        """
        from pypinyin import Style, lazy_pinyin

        # List-returning callbacks preserve alignment but are missing from pypinyin's released type stub.
        return lazy_pinyin(prompt, style=Style.NORMAL, errors=list)  # type: ignore[ty:invalid-argument-type]

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert Hanzi characters in the prompt to Pinyin.

        Args:
            prompt (str): The text prompt to convert.
            input_type (PromptDataType): The input data type. Only ``text`` is supported.

        Returns:
            ConverterResult: The converted prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        han_indices = [i for i, ch in enumerate(prompt) if _HAN_PATTERN.match(ch)]
        if not han_indices:
            return ConverterResult(output_text=prompt, output_type="text")

        rng = self._get_random_generator(stream="pinyin-selection")
        count = round(self._proportion * len(han_indices))
        selected = set(rng.sample(han_indices, count)) if count else set()
        if not selected:
            return ConverterResult(output_text=prompt, output_type="text")

        readings = await asyncio.to_thread(self._get_pinyin_readings, prompt)
        out: list[str] = []
        for i, (ch, reading) in enumerate(zip(prompt, readings, strict=True)):
            if i not in selected:
                out.append(ch)
                continue

            reading = reading or ch
            if self._mode == "initial":
                reading = reading[0]
            elif self._mode == "mixed":
                reading = rng.choice((reading, reading[0]))

            out.append(reading)
            if self._separator and reading != ch and i < len(prompt) - 1:
                out.append(self._separator)

        return ConverterResult(output_text="".join(out), output_type="text")
