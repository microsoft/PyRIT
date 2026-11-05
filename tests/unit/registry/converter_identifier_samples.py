# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Sample values and declared exemptions for the converter identifier contract test."""

from __future__ import annotations

import random
import re
from itertools import product
from pathlib import Path
from typing import Any

from PIL import Image

from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.converter import CodeAttackConverter
from pyrit.converter.text_selection_strategy import (
    AllWordsSelectionStrategy,
    ContentWordSelectionStrategy,
    IndexSelectionStrategy,
    KeywordSelectionStrategy,
    PositionSelectionStrategy,
    ProportionSelectionStrategy,
    RangeSelectionStrategy,
    RegexSelectionStrategy,
    TokenSelectionStrategy,
    WordIndexSelectionStrategy,
    WordKeywordSelectionStrategy,
    WordPositionSelectionStrategy,
    WordProportionSelectionStrategy,
    WordRegexSelectionStrategy,
)
from pyrit.datasets import TextJailBreak
from pyrit.models import SeedPrompt


def _reverse(text: str) -> str:
    return text[::-1]


def _token() -> str:
    return "token"


def _repeat(times: int) -> Any:
    return lambda text: text * times


_SAME_QUALNAME_FUNCTIONS = (
    lambda text: text.upper(),
    lambda text: text.lower(),
)


_RETRIES = "controls how often the request is retried, not what the converter produces"
_AZURE_SPEECH_RESOURCE = (
    "selects which Azure Speech resource serves the request, not how the text or audio is converted"
)
_AZURE_SPEECH_KEY = "a credential, and credentials are never part of an identifier"

NEUTRAL_SETTINGS: dict[tuple[str, str], str] = {
    ("LLMGenericTextConverter", "max_retry_attempts"): _RETRIES,
    ("LLMGenericTextConverter", "retry_wait_max_seconds"): _RETRIES,
    ("LLMGenericTextConverter", "retry_exceptions"): _RETRIES,
    ("TranslationConverter", "max_retries"): _RETRIES,
    ("TranslationConverter", "max_wait_time_in_seconds"): _RETRIES,
    ("MathObfuscationConverter", "rng"): (
        "a random.Random is run-time state, not a setting: its state moves with every draw, so one instance gives "
        "different coefficients on its next call and there is no fixed value to record. The range it draws from, "
        "min_n..max_n, is identified."
    ),
    ("AzureSpeechAudioToTextConverter", "azure_speech_region"): _AZURE_SPEECH_RESOURCE,
    ("AzureSpeechAudioToTextConverter", "azure_speech_resource_id"): _AZURE_SPEECH_RESOURCE,
    ("AzureSpeechAudioToTextConverter", "azure_speech_key"): _AZURE_SPEECH_KEY,
    ("AzureSpeechTextToAudioConverter", "azure_speech_region"): _AZURE_SPEECH_RESOURCE,
    ("AzureSpeechTextToAudioConverter", "azure_speech_resource_id"): _AZURE_SPEECH_RESOURCE,
    ("AzureSpeechTextToAudioConverter", "azure_speech_key"): _AZURE_SPEECH_KEY,
}

SINGLE_VALUE_SETTINGS: dict[tuple[str, str], str] = {
    (name, "output_format"): "only 'wav' is accepted, so there is nothing to vary"
    for name in (
        "AudioEchoConverter",
        "AudioFrequencyConverter",
        "AudioSpeedConverter",
        "AudioVolumeConverter",
        "AudioWhiteNoiseConverter",
    )
}

KNOWN_GAPS: dict[tuple[str, str], str] = {
    ("CodeChameleonConverter", "encrypt_function"): "custom functions are not identified yet, see #3761",
    ("CodeChameleonConverter", "decrypt_function"): "custom functions are not identified yet, see #3761",
    ("PolicyPuppetryConverter", "prompt_template"): "custom templates are identified by name only, see #3759",
    ("TextJailbreakConverter", "jailbreak_template"): "inline templates share one identifier, see #3760",
}

BASELINE_SETTINGS: dict[str, dict[str, Any]] = {
    "AzureSpeechAudioToTextConverter": {"azure_speech_region": "eastus", "azure_speech_key": "test-key"},
    "AzureSpeechTextToAudioConverter": {"azure_speech_region": "eastus", "azure_speech_key": "test-key"},
}

_CUSTOM_CODECHAMELEON = {"encrypt_type": "custom", "encrypt_function": _reverse, "decrypt_function": _reverse}
_CUSTOM_CODE_ATTACK = {
    "template": Path(CONVERTER_SEED_PROMPT_PATH) / "code_attack_python_list.yaml",
    "encoding": CodeAttackConverter.Encoding.PYTHON_LIST,
}

SETTINGS_THAT_NEED_ANOTHER: dict[tuple[str, str], dict[str, Any]] = {
    ("DecompositionConverter", "codewords"): {"use_word_game": True},
    ("DecompositionConverter", "word_game_prompt"): {"use_word_game": True},
    ("SelectiveTextConverter", "word_separator"): {"selection_strategy": WordIndexSelectionStrategy(indices=[0])},
    ("ImagePromptStyleConverter", "variation"): {"filter_name": "public_space_tv_broadcast"},
    ("CodeChameleonConverter", "encrypt_function"): _CUSTOM_CODECHAMELEON,
    ("CodeChameleonConverter", "decrypt_function"): _CUSTOM_CODECHAMELEON,
    ("CodeAttackConverter", "encoding"): _CUSTOM_CODE_ATTACK,
    ("CodeAttackConverter", "template"): _CUSTOM_CODE_ATTACK,
    **{
        (name, "azure_speech_resource_id"): {"azure_speech_key": _token, "azure_speech_resource_id": "resource"}
        for name in ("AzureSpeechAudioToTextConverter", "AzureSpeechTextToAudioConverter")
    },
}

ALLOWED_VALUES_NEED: dict[tuple[str, str], dict[str, Any]] = {
    ("CodeAttackConverter", "encoding"): {"template": _CUSTOM_CODE_ATTACK["template"]},
}

SETTINGS_A_MODE_REPLACES: dict[tuple[str, str], set[str]] = {
    ("SATAMaskingConverter", "selection_strategy"): {
        "num_masks",
        "skip_first",
        "min_word_length",
        "stopwords",
        "candidate_words",
    },
}

_MANY_WORDS = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]

_PROPORTIONS = (
    {"start_proportion": 0.1, "end_proportion": 0.5},
    {"start_proportion": 0.2, "end_proportion": 0.5},
    {"start_proportion": 0.1, "end_proportion": 0.6},
)

SELECTION_STRATEGY_SETTINGS: dict[type, tuple[dict[str, Any], ...]] = {
    IndexSelectionStrategy: ({"end": 3}, {"end": 4}, {"start": 1, "end": 3}, {"end": 0}, {}),
    RegexSelectionStrategy: ({"pattern": "a"}, {"pattern": "b"}, {"pattern": re.compile("a", re.IGNORECASE)}),
    KeywordSelectionStrategy: (
        {"keyword": "a"},
        {"keyword": "b"},
        {"keyword": "A"},
        {"keyword": "a", "context_before": 1},
        {"keyword": "a", "context_after": 1},
        {"keyword": "a", "case_sensitive": False},
    ),
    PositionSelectionStrategy: _PROPORTIONS,
    RangeSelectionStrategy: _PROPORTIONS,
    ProportionSelectionStrategy: (
        {"proportion": 0.5},
        {"proportion": 0.6},
        {"proportion": 0.5, "anchor": "end"},
        {"proportion": 0.5, "seed": 1},
        {"proportion": 0.5, "seed": 0},
    ),
    TokenSelectionStrategy: ({},),
    WordIndexSelectionStrategy: ({"indices": [0, 1]}, {"indices": [0, 2]}, {"indices": [1, 2]}, {"indices": [1, 0]}),
    WordKeywordSelectionStrategy: (
        {"keywords": ["a", "b"]},
        {"keywords": ["a", "c"]},
        {"keywords": ["b", "c"]},
        {"keywords": ["A", "b"]},
        {"keywords": _MANY_WORDS},
        {"keywords": ["a", "b"], "case_sensitive": False},
    ),
    WordProportionSelectionStrategy: (
        {"proportion": 0.5},
        {"proportion": 0.6},
        {"proportion": 0.5, "seed": 1},
        {"proportion": 0.5, "seed": 0},
    ),
    WordRegexSelectionStrategy: ({"pattern": "a"}, {"pattern": "b"}, {"pattern": re.compile("a", re.IGNORECASE)}),
    WordPositionSelectionStrategy: _PROPORTIONS,
    AllWordsSelectionStrategy: ({},),
    ContentWordSelectionStrategy: (
        {},
        {"max_words": 3},
        {"skip_first": 2},
        {"min_word_length": 4},
        {"stopwords": ["a", "b"]},
        {"stopwords": ["a", "c"]},
        {"stopwords": ["b", "c"]},
        {"stopwords": _MANY_WORDS},
        {"candidate_words": ["a", "b"]},
        {"candidate_words": ["a", "c"]},
        {"candidate_words": ["b", "c"]},
        {"candidate_words": _MANY_WORDS},
        {"candidate_words": []},
    ),
}

KEYWORD_SETTINGS: dict[str, tuple[dict[str, Any], ...]] = {
    "LLMGenericTextConverter": ({"tone": "first"}, {"tone": "third"}, {"style": "first"}),
}


class _Vocabulary:
    def __init__(self, letters: str) -> None:
        self._tokens = {"".join(token): index for index, token in enumerate(product(letters, repeat=3))}

    def get_vocab(self) -> dict[str, int]:
        return self._tokens


def make_samples(folder: Path) -> dict[str, tuple[Any, ...]]:
    images = []
    for index in range(20):
        (folder / f"image_{index}").mkdir(exist_ok=True)
        png, jpg = folder / f"image_{index}" / "image.png", folder / f"image_{index}" / "image.jpg"
        Image.new("RGB", (32, 32), (index * 10, 0, 0)).save(png)
        Image.new("RGB", (32, 32), (index * 10, 0, 0)).save(jpg)
        images.append((str(png), str(jpg)))
    documents = []
    for name in ("first_document", "second_document"):
        (folder / name).mkdir(exist_ok=True)
        for suffix in ("pdf", "docx"):
            (folder / name / f"document.{suffix}").write_bytes(name.encode())
            documents.append(folder / name / f"document.{suffix}")
    pngs, jpgs = [png for png, _ in images], [jpg for _, jpg in images]
    other_png, other_jpg = folder / "image_0" / "other.png", folder / "image_0" / "other.jpg"
    Image.new("RGB", (32, 32), (0, 255, 0)).save(other_png)
    Image.new("RGB", (32, 32), (0, 255, 0)).save(other_jpg)
    other_pdf, other_docx = folder / "first_document" / "other.pdf", folder / "first_document" / "other.docx"
    other_pdf.write_bytes(b"other")
    other_docx.write_bytes(b"other")
    styles = Path(CONVERTER_SEED_PROMPT_PATH) / "image_prompt_style"
    filters = []
    for subfolder, source in (("first", "gritty_documentary.yaml"), ("second", "laundromat_fisheye.yaml")):
        (folder / subfolder).mkdir(exist_ok=True)
        filters.append(folder / subfolder / "custom.yaml")
        filters[-1].write_bytes((styles / source).read_bytes())
    code_attack = _CUSTOM_CODE_ATTACK["template"].read_text(encoding="utf-8")
    (folder / "first_code_attack").mkdir(exist_ok=True)
    (folder / "second_code_attack").mkdir(exist_ok=True)
    code_attack_templates = (
        folder / "first_code_attack" / "custom.yaml",
        folder / "second_code_attack" / "custom.yaml",
    )
    code_attack_templates[0].write_text(
        code_attack.replace("{{ wrapped_input }}", "{{ wrapped_input }}  ", 1), encoding="utf-8"
    )
    code_attack_templates[1].write_text(
        code_attack.replace("{{ wrapped_input }}", "{{ wrapped_input }} ", 1), encoding="utf-8"
    )
    return {
        "img_to_add": (pngs[0], pngs[1], str(other_png)),
        "base_image": (pngs[0], pngs[1], str(other_png)),
        "carrier_image_path": (pngs[0], pngs[1], str(other_png)),
        "benign_image_path": (jpgs[0], jpgs[1], str(other_jpg)),
        "innocuous_images": (pngs[:19], [pngs[19], *pngs[1:19]], [*pngs[:18], pngs[19]]),
        "payload_background": (pngs[0], pngs[1], str(other_png)),
        "PDFConverter.existing_pdf": (documents[0], documents[2], other_pdf),
        "WordDocConverter.existing_docx": (documents[1], documents[3], other_docx),
        "PDFConverter.prompt_template": (
            SeedPrompt(value="First {{ prompt }}", parameters=["prompt"]),
            SeedPrompt(value="Second {{ prompt }}", parameters=["prompt"]),
        ),
        "WordDocConverter.prompt_template": (
            SeedPrompt(value="First {{ prompt }}", parameters=["prompt"]),
            SeedPrompt(value="Second {{ prompt }}", parameters=["prompt"]),
        ),
        "LLMGenericTextConverter.system_prompt_template": (SeedPrompt(value="First"), SeedPrompt(value="Second")),
        "TranslationConverter.prompt_template": (
            SeedPrompt(value="First {{ language }}", parameters=["language"]),
            SeedPrompt(value="Second {{ language }}", parameters=["language"]),
        ),
        "LLMGenericTextConverter.user_prompt_template_with_objective": (
            SeedPrompt(value="First {{ objective }}", parameters=["objective"]),
            SeedPrompt(value="Second {{ objective }}", parameters=["objective"]),
        ),
        "AddImageTextConverter.bounding_box": (
            (0, 0, 10, 10),
            (1, 0, 10, 10),
            (0, 1, 10, 10),
            (0, 0, 11, 10),
            (0, 0, 10, 11),
        ),
        "AddImageTextConverter.font_size": (16, (18, 30), (19, 30), (18, 31)),
        "CodeAttackConverter.template": code_attack_templates,
        "CodeAttackConverter.encoding": tuple(CodeAttackConverter.Encoding),
        "ScientificTranslationConverter.mode": ("academic", "technical"),
        "RepeatTokenConverter.token_insert_mode": ("prepend", "append", "repeat"),
        "SATAMaskingConverter.num_masks": (3, 4),
        "SATAMaskingConverter.skip_first": (2, 3),
        "video_path": ("first/video.mp4", "second/video.mp4", "first/other.mp4"),
        "wordswap_path": ("filipino.yaml", "indian.yaml"),
        "font_name": (Path("first/font.ttf"), Path("second/font.ttf"), Path("first/other.ttf")),
        "filter_path": tuple(filters),
        "injection_items": (
            [{"text": "first"}, {"text": "second"}],
            [{"text": "first"}, {"text": "third"}],
            [{"text": "second"}, {"text": "third"}],
            [{"text": "first", "page": 1}, {"text": "second"}],
            [{"text": "first", "x": 20}, {"text": "second"}],
            [{"text": "first", "font_size": 20}, {"text": "second"}],
        ),
        "encrypt_function": (*_SAME_QUALNAME_FUNCTIONS, _repeat(2), _repeat(3)),
        "decrypt_function": _SAME_QUALNAME_FUNCTIONS,
        "filter_name": ("laundromat_fisheye", "polaroid_vintage_film"),
        "variation": ("dive_bar_reflection", "airport_lounge"),
        "replace": ("first", "second", ["first", "second"], ["first", "third"], ["second", "third"]),
        "zero_char": ("\u200b", "\u200c"),
        "one_char": ("\u200d", "\u2060"),
        "base_char_utf8": ("a", "b"),
        "custom_substitutions": ({"a": ["4"]}, {"a": ["@"]}, {"b": ["4"]}),
        "stopwords": (["the", "of"], ["the", "in"], ["of", "in"]),
        "candidate_words": (["alpha", "beta"], ["alpha", "gamma"], ["beta", "gamma"]),
        "template": ("First {{ prompt }}", "Second {{ prompt }}", "First {{ prompt }} now"),
        "seed": (0, 1),
        "random_seed": (0, 1),
        "CaesarConverter.caesar_offset": (1, -1, 2),
        "trap_template": ("{prompt} is {wrong_value}", "Is {prompt} {wrong_value}?"),
        "encrypt_type": ("reverse", "binary_tree"),
        "persuasion_technique": ("authority_endorsement", "expert_endorsement"),
        "tokenizer": (_Vocabulary("abcdefgh"), _Vocabulary("ijklmnop")),
        "MathObfuscationConverter.rng": (random.Random(1), random.Random(2)),
        "retry_exceptions": ((ValueError,), (KeyError,)),
        "SearchReplaceConverter.regex_flags": (re.IGNORECASE, re.MULTILINE),
        "jailbreak_template": (
            TextJailBreak(string_template="Say first {{ prompt }} now"),
            TextJailBreak(string_template="Say third {{ prompt }} now"),
        ),
        "PolicyPuppetryConverter.prompt_template": (
            SeedPrompt(value="Say first {{ prompt }} now", parameters=["prompt"]),
            SeedPrompt(value="Say third {{ prompt }} now", parameters=["prompt"]),
        ),
    }
