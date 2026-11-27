# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# Portions Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# Garak-derived portions are licensed under Apache-2.0 and modified by Microsoft Corporation.
# See THIRD_PARTY_NOTICES.txt for attribution and source details.
# The disguise and word-guide method, templates, letter map, word list, and tool list
# originate from LLM-DRA/DRA commit 2c33d9b8d71f8af1c42fb7c77fd504e7015c3a6f
# (MIT, Copyright (c) 2024 LLM-DRA), by way of garak.

import enum
import functools
import hashlib
import pathlib
import random
from dataclasses import dataclass

from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.common.yaml_helper import load_yaml_file
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.models import ComponentIdentifier, PromptDataType, SeedDataset, SeedPrompt

_TEMPLATE_PATH = pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "dra_converter.yaml"
_WORDS_PATH = pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "dra_converter_words.yaml"
_TEMPLATE_PARAMETERS = frozenset({"word_guide", "tools"})


class DRATemplate(enum.Enum):
    """
    Selectable DRA answer-forcing templates.

    Each member maps to a named template in ``dra_converter.yaml``. The ``PREPARE_TOOLS``
    templates also name two tools the answer should start by preparing; the ``STRICT``
    templates use the paper's firmer wording for Llama models.
    """

    PREPARE_TOOLS = "prepare_tools"
    CHOOSE_TARGET = "choose_target"
    STRICT_PREPARE_TOOLS = "strict_prepare_tools"
    STRICT_CHOOSE_TARGET = "strict_choose_target"

    def to_seed_prompt(self) -> SeedPrompt:
        """
        Load the ``SeedPrompt`` template backing this enum member.

        Returns:
            SeedPrompt: The template prompt for this member.
        """
        return _load_templates()[self.value]


@dataclass(frozen=True)
class _DRAWords:
    letter_map: dict[str, tuple[str, ...]]
    tools: tuple[str, ...]
    flagged_words: frozenset[str]


@functools.lru_cache(maxsize=1)
def _load_templates() -> dict[str, SeedPrompt]:
    """
    Load and cache the DRA templates keyed by name.

    Returns:
        dict[str, SeedPrompt]: Mapping of template name to its SeedPrompt.
    """
    dataset = SeedDataset.from_yaml_file(_TEMPLATE_PATH)
    return {str(prompt.name): prompt for prompt in dataset.prompts}


@functools.lru_cache(maxsize=1)
def _load_words() -> _DRAWords:
    """
    Load and cache the letter map, tool phrases, and flagged words.

    Returns:
        _DRAWords: The DRA word data.
    """
    data = load_yaml_file(path=_WORDS_PATH)
    return _DRAWords(
        letter_map={letter: tuple(phrases) for letter, phrases in data["letter_map"].items()},
        tools=tuple(data["tools"]),
        flagged_words=frozenset(data["flagged_words"]),
    )


class DRAConverter(Converter):
    """
    Disguises a prompt letter by letter and asks the target to rebuild and answer it.

    The Disguise and Reconstruction Attack (DRA) [@liu2024dra] spells the lowercased prompt one
    character per line, each hidden in parentheses inside a harmless phrase such as ``happy (h)our``.
    It then appends a template with a word guide (the prompt's words, with flagged words cut short)
    and instructions to rebuild the request and open the answer with an agreeing sentence. No single
    line contains the request.

    This is garak's single-shot version, without the paper's retry that cuts words shorter after a
    refusal. A word counts as flagged when it exactly matches the paper's list of words that
    Detoxify scored as toxic.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(
        self,
        *,
        prompt_template: SeedPrompt | None = None,
        harmful_keep_ratio: float = 0.5,
        benign_keep_ratio: float = 0.5,
        benign_full_word_probability: float = 0.33,
        seed: int | None = None,
    ) -> None:
        """
        Initialize the converter.

        Args:
            prompt_template (SeedPrompt | None): The template appended after the disguised prompt, such
                as ``DRATemplate.PREPARE_TOOLS.to_seed_prompt()``. It must declare a ``word_guide``
                parameter and may also declare ``tools``. If not provided, each conversion picks a
                ``DRATemplate`` from the seeded random stream.
            harmful_keep_ratio (float): Lowest share of a flagged word of 5 or more characters to keep.
                The cut falls at a random index from ``max(2, int(length * harmful_keep_ratio))``
                to ``length - 1``, so a higher value keeps more of the word. Must be between 0 and 1,
                exclusive.
            benign_keep_ratio (float): Lowest share of a cut benign word to keep. The cut falls at a
                random index from ``min(length * 2 // 3, int(length * benign_keep_ratio))`` to
                ``length - 1``, and never before the first character, so values above two thirds have
                no effect. Must be between 0 and 1, exclusive.
            benign_full_word_probability (float): Probability that a benign word of 4 or more characters
                is kept whole. Lower values give the target fewer hints. Must be between 0 and 1,
                inclusive.
            seed (int | None): Seed for the template pick and the truncation. Pass an int for
                reproducible output; leave as None to inherit the root configured by
                ``initialize_pyrit_async(seed=...)``.

        Raises:
            TypeError: If ``prompt_template`` is not a ``SeedPrompt`` or None.
            ValueError: If ``prompt_template`` does not declare ``word_guide`` or declares parameters
                other than ``word_guide`` and ``tools``, if a ratio is not between 0 and 1, exclusive,
                or if the probability is not between 0 and 1, inclusive.
        """
        super().__init__()
        if prompt_template is not None:
            if not isinstance(prompt_template, SeedPrompt):
                raise TypeError(f"prompt_template must be a SeedPrompt or None, got {prompt_template!r}.")
            declared = set(prompt_template.parameters or [])
            if "word_guide" not in declared or not declared <= _TEMPLATE_PARAMETERS:
                raise ValueError(
                    "prompt_template must declare a word_guide parameter and may also declare tools, "
                    f"got {sorted(declared)}."
                )
        for name, ratio in (("harmful_keep_ratio", harmful_keep_ratio), ("benign_keep_ratio", benign_keep_ratio)):
            if not 0 < ratio < 1:
                raise ValueError(f"{name} must be between 0 and 1, exclusive, got {ratio}.")
        if not 0 <= benign_full_word_probability <= 1:
            raise ValueError(
                f"benign_full_word_probability must be between 0 and 1, inclusive, got {benign_full_word_probability}."
            )

        self._prompt_template = prompt_template
        self._harmful_keep_ratio = harmful_keep_ratio
        self._benign_keep_ratio = benign_keep_ratio
        self._benign_full_word_probability = benign_full_word_probability
        self._seed = seed
        self._templates = _load_templates()
        self._words = _load_words()

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier including the template and truncation settings.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        template = self._prompt_template
        return self._create_identifier(
            params={
                "template": template.name if template else None,
                "template_hash": (
                    hashlib.sha256(str(template.value).encode("utf-8")).hexdigest()[:16] if template else None
                ),
                "harmful_keep_ratio": self._harmful_keep_ratio,
                "benign_keep_ratio": self._benign_keep_ratio,
                "benign_full_word_probability": self._benign_full_word_probability,
                "seed": self._seed,
            }
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Disguise the prompt and append the rendered DRA template.

        Args:
            prompt (str): The request to disguise.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The disguise lines followed by the rendered template.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError(f"Input type {input_type} not supported")

        template = (
            self._prompt_template
            or self._templates[self._get_random_generator(stream="template").choice(list(DRATemplate)).value]
        )
        question = prompt.lower()
        disguise = self._disguise(question=question, rng=self._get_random_generator(stream="disguise"))
        word_guide = self._word_guide(question=question, rng=self._get_random_generator(stream="word_guide"))
        tools = " and ".join(self._get_random_generator(stream="tools").sample(self._words.tools, 2))
        suffix = template.render_template_value(word_guide=word_guide, tools=tools)
        return ConverterResult(output_text=disguise + suffix, output_type="text")

    def _disguise(self, *, question: str, rng: random.Random) -> str:
        lines = []
        for character in question:
            phrases = self._words.letter_map.get(character)
            if not phrases:
                lines.append(f"({character})")
                continue
            phrase = rng.choice(phrases)
            index = rng.choice([i for i, letter in enumerate(phrase) if letter == character])
            lines.append(f"{phrase[:index]}({character}){phrase[index + 1 :]}")
        return "".join(f"{line}\n" for line in lines)

    def _word_guide(self, *, question: str, rng: random.Random) -> str:
        entries = []
        for word in question.split():
            if word in self._words.flagged_words:
                entries.extend(self._flagged_word_entries(word=word, rng=rng))
            else:
                entries.extend(self._benign_word_entries(word=word, rng=rng))
        return "".join(f'"{entry}" ' for entry in entries)

    def _flagged_word_entries(self, *, word: str, rng: random.Random) -> list[str]:
        if len(word) <= 2:
            return [word]
        if len(word) <= 4:
            return [word[:-1] if word[-1].isalpha() else word[:-2]]
        start = max(2, int(len(word) * self._harmful_keep_ratio))
        return self._cut(word=word, start=start, rng=rng)

    def _benign_word_entries(self, *, word: str, rng: random.Random) -> list[str]:
        if len(word) <= 3 or rng.random() < self._benign_full_word_probability:
            return [word]
        # garak can cut at index 0 for a small ratio, which gives an empty hint; keep at least one character.
        start = max(1, min(len(word) * 2 // 3, int(len(word) * self._benign_keep_ratio)))
        return self._cut(word=word, start=start, rng=rng)

    @staticmethod
    def _cut(*, word: str, start: int, rng: random.Random) -> list[str]:
        index = rng.randint(start, len(word) - 1)
        entries = [word[:index]]
        if rng.random() < 0.5:
            entries.append(word[index:])
        return entries
