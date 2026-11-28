# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# Portions Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# Garak-derived portions are licensed under Apache-2.0 and modified by Microsoft Corporation.
# See THIRD_PARTY_NOTICES.txt for attribution and source details.

from typing import ClassVar

from pyrit.common.text_helper import graphemes
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType

# Bidirectional control characters, built from code points to keep the source file pure ASCII
_LRO = chr(0x202D)  # LEFT-TO-RIGHT OVERRIDE
_RLO = chr(0x202E)  # RIGHT-TO-LEFT OVERRIDE
_PDF = chr(0x202C)  # POP DIRECTIONAL FORMATTING
_LRI = chr(0x2066)  # LEFT-TO-RIGHT ISOLATE
_PDI = chr(0x2069)  # POP DIRECTIONAL ISOLATE


def _render_swap(*, first: str, second: str) -> str:
    """
    Store two adjacent characters in reverse order so they still display in their original order.

    The control sequence follows garak's ``_render_swaps``, after the bidi swap from Boucher et al.,
    "Bad Characters: Imperceptible NLP Attacks". A right-to-left override reverses the display of two
    left-to-right isolates, and the outer override and isolate keep that effect from reaching the
    surrounding text.

    Args:
        first (str): The character that displays first.
        second (str): The character that displays second.

    Returns:
        str: The two characters in swapped logical order, wrapped in bidi controls.
    """
    return f"{_LRO}{_LRI}{_RLO}{_LRI}{second}{_PDI}{_LRI}{first}{_PDI}{_PDF}{_PDI}{_PDF}"


class BidiSwapConverter(Converter):
    """
    Swaps adjacent characters in the stored text while bidirectional controls keep the displayed text the same.

    For each swapped pair, the two characters are stored in reverse order and wrapped in Unicode
    bidirectional control characters that make them render in their original order. A human reader sees
    the original text, but a model reading the code points sees the pair reversed. This is the reordering
    perturbation from Boucher et al., "Bad Characters: Imperceptible NLP Attacks" (arXiv:2106.09898), as
    implemented by garak's BadCharacters probe.

    ``BidiConverter`` reverses the display of the whole prompt instead.

    Characters are grapheme clusters, so combining marks, Indic vowel signs and emoji sequences stay whole.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    #: Every control character the converter can emit, for callers that need to strip them.
    CONTROL_CHARACTERS: ClassVar[frozenset[str]] = frozenset({_LRO, _RLO, _PDF, _LRI, _PDI})

    def __init__(
        self,
        *,
        budget: int = 1,
        seed: int | None = None,
    ) -> None:
        """
        Initialize the converter.

        Args:
            budget (int): How many adjacent pairs to swap. Pairs never share a character and are chosen at
                random. If the text has room for fewer pairs, as many as fit are swapped. Defaults to 1.
            seed (int | None): Optional seed for reproducible pair positions. Defaults to None.

        Raises:
            ValueError: If ``budget`` is not a positive integer.
        """
        super().__init__()

        if isinstance(budget, bool) or budget < 1:
            raise ValueError(f"budget must be a positive integer, got {budget!r}")

        self._budget = budget
        self._seed = seed

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with the swap parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(params={"budget": self._budget, "seed": self._seed})

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the given prompt by swapping adjacent character pairs behind bidi controls.

        Args:
            prompt (str): The prompt to be converted.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing the modified prompt.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Only 'text' input type is supported.")

        clusters = graphemes(prompt)
        starts = self._choose_pair_starts(cluster_count=len(clusters))

        pieces: list[str] = []
        index = 0
        while index < len(clusters):
            if index in starts:
                pieces.append(_render_swap(first=clusters[index], second=clusters[index + 1]))
                index += 2
            else:
                pieces.append(clusters[index])
                index += 1

        return ConverterResult(output_text="".join(pieces), output_type="text")

    def _choose_pair_starts(self, *, cluster_count: int) -> set[int]:
        """
        Choose the start index of each swapped pair, uniformly over all non-overlapping placements.

        Choosing ``k`` sorted indices from ``cluster_count - k`` slots and shifting the ``i``-th one right by
        ``i`` leaves at least one index between consecutive starts, so no two pairs share a character.

        Args:
            cluster_count (int): The number of grapheme clusters in the text.

        Returns:
            set[int]: The index of the first character of each pair.
        """
        pair_count = min(self._budget, cluster_count // 2)
        if pair_count == 0:
            return set()

        rng = self._get_random_generator(stream="bidi-swap-positions")
        slots = sorted(rng.sample(range(cluster_count - pair_count), pair_count))
        return {slot + offset for offset, slot in enumerate(slots)}
