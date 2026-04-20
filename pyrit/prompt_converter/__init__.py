# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Prompt converters for transforming prompts before sending them to targets in red teaming workflows.

Converters are organized into categories: Text-to-Text (encoding, obfuscation, translation, variation),
Audio (text-to-audio, audio-to-text, audio-to-audio), Image (text-to-image, image-to-image),
Video (image-to-video), File (text-to-PDF/URL), Selective Converting (partial prompt transformation),
and Human-in-the-Loop (interactive review). Converters can be stacked together to create complex
transformation pipelines for testing AI system robustness.
"""

from pyrit.prompt_converter.add_image_text_converter import AddImageTextConverter
from pyrit.prompt_converter.add_image_to_video_converter import AddImageVideoConverter
from pyrit.prompt_converter.add_text_image_converter import AddTextImageConverter
from pyrit.prompt_converter.ansi_escape.ansi_attack_converter import AnsiAttackConverter
from pyrit.prompt_converter.ascii_art_converter import AsciiArtConverter
from pyrit.prompt_converter.ask_to_decode_converter import AskToDecodeConverter
from pyrit.prompt_converter.atbash_converter import AtbashConverter
from pyrit.prompt_converter.audio_frequency_converter import AudioFrequencyConverter
from pyrit.prompt_converter.azure_speech_audio_to_text_converter import AzureSpeechAudioToTextConverter
from pyrit.prompt_converter.azure_speech_text_to_audio_converter import AzureSpeechTextToAudioConverter
from pyrit.prompt_converter.base64_converter import Base64Converter
from pyrit.prompt_converter.base2048_converter import Base2048Converter
from pyrit.prompt_converter.bin_ascii_converter import BinAsciiConverter
from pyrit.prompt_converter.binary_converter import BinaryConverter
from pyrit.prompt_converter.braille_converter import BrailleConverter
from pyrit.prompt_converter.caesar_converter import CaesarConverter
from pyrit.prompt_converter.character_space_converter import CharacterSpaceConverter
from pyrit.prompt_converter.charswap_attack_converter import CharSwapConverter
from pyrit.prompt_converter.codechameleon_converter import CodeChameleonConverter
from pyrit.prompt_converter.colloquial_wordswap_converter import ColloquialWordswapConverter
from pyrit.prompt_converter.denylist_converter import DenylistConverter
from pyrit.prompt_converter.diacritic_converter import DiacriticConverter
from pyrit.prompt_converter.ecoji_converter import EcojiConverter
from pyrit.prompt_converter.emoji_converter import EmojiConverter
from pyrit.prompt_converter.first_letter_converter import FirstLetterConverter
from pyrit.prompt_converter.flip_converter import FlipConverter
from pyrit.prompt_converter.human_in_the_loop_converter import HumanInTheLoopConverter
from pyrit.prompt_converter.image_compression_converter import ImageCompressionConverter
from pyrit.prompt_converter.insert_punctuation_converter import InsertPunctuationConverter
from pyrit.prompt_converter.leetspeak_converter import LeetspeakConverter
from pyrit.prompt_converter.llm_generic_text_converter import LLMGenericTextConverter
from pyrit.prompt_converter.malicious_question_generator_converter import MaliciousQuestionGeneratorConverter
from pyrit.prompt_converter.math_obfuscation_converter import MathObfuscationConverter
from pyrit.prompt_converter.math_prompt_converter import MathPromptConverter
from pyrit.prompt_converter.morse_converter import MorseConverter
from pyrit.prompt_converter.nato_converter import NatoConverter
from pyrit.prompt_converter.negation_trap_converter import NegationTrapConverter
from pyrit.prompt_converter.noise_converter import NoiseConverter
from pyrit.prompt_converter.pdf_converter import PDFConverter
from pyrit.prompt_converter.persuasion_converter import PersuasionConverter
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter, get_converter_modalities
from pyrit.prompt_converter.qr_code_converter import QRCodeConverter
from pyrit.prompt_converter.random_capital_letters_converter import RandomCapitalLettersConverter
from pyrit.prompt_converter.random_translation_converter import RandomTranslationConverter
from pyrit.prompt_converter.repeat_token_converter import RepeatTokenConverter
from pyrit.prompt_converter.rot13_converter import ROT13Converter
from pyrit.prompt_converter.search_replace_converter import SearchReplaceConverter
from pyrit.prompt_converter.selective_text_converter import SelectiveTextConverter
from pyrit.prompt_converter.string_join_converter import StringJoinConverter
from pyrit.prompt_converter.suffix_append_converter import SuffixAppendConverter
from pyrit.prompt_converter.superscript_converter import SuperscriptConverter
from pyrit.prompt_converter.template_segment_converter import TemplateSegmentConverter
from pyrit.prompt_converter.tense_converter import TenseConverter
from pyrit.prompt_converter.text_jailbreak_converter import TextJailbreakConverter
from pyrit.prompt_converter.text_selection_strategy import (
    AllWordsSelectionStrategy,
    IndexSelectionStrategy,
    KeywordSelectionStrategy,
    PositionSelectionStrategy,
    ProportionSelectionStrategy,
    RangeSelectionStrategy,
    RegexSelectionStrategy,
    TextSelectionStrategy,
    TokenSelectionStrategy,
    WordIndexSelectionStrategy,
    WordKeywordSelectionStrategy,
    WordPositionSelectionStrategy,
    WordProportionSelectionStrategy,
    WordRegexSelectionStrategy,
    WordSelectionStrategy,
)
from pyrit.prompt_converter.token_smuggling import (
    AsciiSmugglerConverter,
    SneakyBitsSmugglerConverter,
    VariationSelectorSmugglerConverter,
)
from pyrit.prompt_converter.tone_converter import ToneConverter
from pyrit.prompt_converter.toxic_sentence_generator_converter import ToxicSentenceGeneratorConverter
from pyrit.prompt_converter.translation_converter import TranslationConverter
from pyrit.prompt_converter.transparency_attack_converter import TransparencyAttackConverter
from pyrit.prompt_converter.unicode_confusable_converter import UnicodeConfusableConverter
from pyrit.prompt_converter.unicode_replacement_converter import UnicodeReplacementConverter
from pyrit.prompt_converter.unicode_sub_converter import UnicodeSubstitutionConverter
from pyrit.prompt_converter.url_converter import UrlConverter
from pyrit.prompt_converter.variation_converter import VariationConverter
from pyrit.prompt_converter.zalgo_converter import ZalgoConverter
from pyrit.prompt_converter.zero_width_converter import ZeroWidthConverter

__all__ = [
    "AddImageTextConverter",
    "AddImageVideoConverter",
    "AddTextImageConverter",
    "AllWordsSelectionStrategy",
    "AnsiAttackConverter",
    "AsciiArtConverter",
    "AsciiSmugglerConverter",
    "AskToDecodeConverter",
    "AtbashConverter",
    "AudioFrequencyConverter",
    "AzureSpeechAudioToTextConverter",
    "AzureSpeechTextToAudioConverter",
    "Base2048Converter",
    "Base64Converter",
    "BinAsciiConverter",
    "BinaryConverter",
    "BrailleConverter",
    "CaesarConverter",
    "CharSwapConverter",
    "CharacterSpaceConverter",
    "CodeChameleonConverter",
    "ColloquialWordswapConverter",
    "ConverterResult",
    "DenylistConverter",
    "DiacriticConverter",
    "EcojiConverter",
    "EmojiConverter",
    "FirstLetterConverter",
    "FlipConverter",
    "HumanInTheLoopConverter",
    "ImageCompressionConverter",
    "IndexSelectionStrategy",
    "InsertPunctuationConverter",
    "KeywordSelectionStrategy",
    "LeetspeakConverter",
    "LLMGenericTextConverter",
    "MaliciousQuestionGeneratorConverter",
    "MathObfuscationConverter",
    "MathPromptConverter",
    "MorseConverter",
    "NatoConverter",
    "NegationTrapConverter",
    "NoiseConverter",
    "PDFConverter",
    "PersuasionConverter",
    "PositionSelectionStrategy",
    "PromptConverter",
    "ProportionSelectionStrategy",
    "QRCodeConverter",
    "ROT13Converter",
    "RandomCapitalLettersConverter",
    "RandomTranslationConverter",
    "RangeSelectionStrategy",
    "RegexSelectionStrategy",
    "RepeatTokenConverter",
    "SearchReplaceConverter",
    "SelectiveTextConverter",
    "SneakyBitsSmugglerConverter",
    "StringJoinConverter",
    "SuffixAppendConverter",
    "SuperscriptConverter",
    "TemplateSegmentConverter",
    "TenseConverter",
    "TextJailbreakConverter",
    "TextSelectionStrategy",
    "TokenSelectionStrategy",
    "ToneConverter",
    "ToxicSentenceGeneratorConverter",
    "TranslationConverter",
    "TransparencyAttackConverter",
    "UnicodeConfusableConverter",
    "UnicodeReplacementConverter",
    "UnicodeSubstitutionConverter",
    "UrlConverter",
    "VariationConverter",
    "VariationSelectorSmugglerConverter",
    "WordIndexSelectionStrategy",
    "WordKeywordSelectionStrategy",
    "WordPositionSelectionStrategy",
    "WordProportionSelectionStrategy",
    "WordRegexSelectionStrategy",
    "WordSelectionStrategy",
    "ZalgoConverter",
    "ZeroWidthConverter",
    "get_converter_modalities",
]
