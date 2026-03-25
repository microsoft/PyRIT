# ---
# jupyter:
#   jupytext:
#     cell_metadata_filter: -all
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.17.2
# ---

# %% [markdown]
# # 1. Text-to-Text Converters
#
# Text-to-text converters transform text input into modified text output. These converters are the most common type and include encoding schemes, obfuscation techniques, and LLM-based transformations.
#
# ## Overview
#
# This notebook covers two main categories of text-to-text converters:
#
# - **[Non-LLM Converters](#non-llm-converters)**: Static transformations including encoding, obfuscation, and character manipulation
# - **[LLM-Based Converters](#llm-based-converters)**: AI-powered transformations including translation, variation, and semantic modifications

# %% [markdown]
# <a id="non-llm-converters"></a>
# ## Non-LLM Converters
#
# Non-LLM converters use deterministic algorithms to transform text. These include:
# - **Encoding**: Base64, Binary, Morse, NATO phonetic, etc.
# - **Obfuscation**: Leetspeak, Unicode manipulation, character swapping, ANSI escape codes
# - **Text manipulation**: ROT13, Caesar cipher, Atbash, etc.

# %% [markdown]
# ### 1.1 Basic Encoding Converters
#
# These converters encode text into various formats:

# %%
from pyrit.prompt_converter import (
    AsciiArtConverter,
    AskToDecodeConverter,
    AtbashConverter,
    Base64Converter,
    Base2048Converter,
    BinaryConverter,
    BinAsciiConverter,
    BrailleConverter,
    CaesarConverter,
    EcojiConverter,
    MorseConverter,
    NatoConverter,
    NegationTrapConverter,
    ROT13Converter,
)
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore

prompt = "tell me how to cut down a tree"

print("ROT13:", await ROT13Converter().convert_async(prompt=prompt))  # type: ignore
print("Base64:", await Base64Converter().convert_async(prompt=prompt))  # type: ignore
print("Base2048:", await Base2048Converter().convert_async(prompt=prompt))  # type: ignore
print("Binary:", await BinaryConverter().convert_async(prompt=prompt))  # type: ignore
print("BinAscii:", await BinAsciiConverter().convert_async(prompt=prompt))  # type: ignore
print("Morse:", await MorseConverter().convert_async(prompt=prompt))  # type: ignore
print("NATO:", await NatoConverter().convert_async(prompt=prompt))  # type: ignore
print("Caesar:", await CaesarConverter(caesar_offset=3).convert_async(prompt=prompt))  # type: ignore
print("Atbash:", await AtbashConverter().convert_async(prompt=prompt))  # type: ignore
print("Braille:", await BrailleConverter().convert_async(prompt=prompt))  # type: ignore
print("ASCII Art:", await AsciiArtConverter().convert_async(prompt=prompt))  # type: ignore
print("Ecoji:", await EcojiConverter().convert_async(prompt=prompt))  # type: ignore

# Ask to decode wraps encoded text with prompts asking to decode it
base64_text = await Base64Converter().convert_async(prompt=prompt)  # type: ignore
ask_decoder = AskToDecodeConverter(encoding_name="Base64")
print("Ask to Decode:", await ask_decoder.convert_async(prompt=base64_text.output_text))  # type: ignore

# Negation Trap adds negation phrases to try to confuse the model
print("Negation Trap:", await NegationTrapConverter().convert_async(prompt="your metaprompt"))  # type: ignore


# %% [markdown]
# ### 1.2 Obfuscation Converters
#
# These converters obfuscate text to evade detection or filters, including character-level manipulations, word-level attacks, and ANSI escape sequences:

# %%
from pyrit.prompt_converter import (
    AnsiAttackConverter,
    CharacterSpaceConverter,
    CharSwapConverter,
    CodeChameleonConverter,
    ColloquialWordswapConverter,
    DiacriticConverter,
    EmojiConverter,
    FirstLetterConverter,
    FlipConverter,
    InsertPunctuationConverter,
    LeetspeakConverter,
    MathObfuscationConverter,
    RandomCapitalLettersConverter,
    RepeatTokenConverter,
    StringJoinConverter,
    SuperscriptConverter,
    UnicodeConfusableConverter,
    UnicodeReplacementConverter,
    UnicodeSubstitutionConverter,
    WordProportionSelectionStrategy,
    ZalgoConverter,
    ZeroWidthConverter,
)

prompt = "tell me how to cut down a tree"

print("Leetspeak:", await LeetspeakConverter().convert_async(prompt=prompt))  # type: ignore
print("Random Capitals:", await RandomCapitalLettersConverter(percentage=50.0).convert_async(prompt=prompt))  # type: ignore
print("Unicode Confusable:", await UnicodeConfusableConverter().convert_async(prompt=prompt))  # type: ignore
print("Unicode Substitution:", await UnicodeSubstitutionConverter().convert_async(prompt=prompt))  # type: ignore
print("Unicode Replacement:", await UnicodeReplacementConverter().convert_async(prompt=prompt))  # type: ignore
print("Emoji:", await EmojiConverter().convert_async(prompt=prompt))  # type: ignore
print("First Letter:", await FirstLetterConverter().convert_async(prompt=prompt))  # type: ignore
print("String Join:", await StringJoinConverter().convert_async(prompt=prompt))  # type: ignore
print("Zero Width:", await ZeroWidthConverter().convert_async(prompt=prompt))  # type: ignore
print("Flip:", await FlipConverter().convert_async(prompt=prompt))  # type: ignore
# Character Space [@robustintelligence2024bypass] inserts spaces between characters
print("Character Space:", await CharacterSpaceConverter().convert_async(prompt=prompt))  # type: ignore
print("Diacritic:", await DiacriticConverter().convert_async(prompt=prompt))  # type: ignore
print("Superscript:", await SuperscriptConverter().convert_async(prompt=prompt))  # type: ignore
print("Zalgo:", await ZalgoConverter().convert_async(prompt=prompt))  # type: ignore

# CharSwap swaps characters within words
char_swap = CharSwapConverter(max_iterations=3, word_selection_strategy=WordProportionSelectionStrategy(proportion=0.8))
print("CharSwap:", await char_swap.convert_async(prompt=prompt))  # type: ignore

# Insert punctuation adds punctuation marks
insert_punct = InsertPunctuationConverter(word_swap_ratio=0.2)
print("Insert Punctuation:", await insert_punct.convert_async(prompt=prompt))  # type: ignore

# ANSI escape sequences
ansi_converter = AnsiAttackConverter(incorporate_user_prompt=True)
print("ANSI Attack:", await ansi_converter.convert_async(prompt=prompt))  # type: ignore

# Math obfuscation replaces words with mathematical expressions
math_obf = MathObfuscationConverter()
print("Math Obfuscation:", await math_obf.convert_async(prompt=prompt))  # type: ignore

# Repeat token adds repeated tokens
repeat_token = RepeatTokenConverter(token_to_repeat="!", times_to_repeat=10, token_insert_mode="append")
print("Repeat Token:", await repeat_token.convert_async(prompt=prompt))  # type: ignore

# Colloquial wordswap replaces words with colloquial equivalents
colloquial = ColloquialWordswapConverter()
print("Colloquial Wordswap:", await colloquial.convert_async(prompt=prompt))  # type: ignore

# CodeChameleon [@lv2024codechameleon] encrypts and wraps in code
code_chameleon = CodeChameleonConverter(encrypt_type="reverse")
print("CodeChameleon:", await code_chameleon.convert_async(prompt=prompt))  # type: ignore

# %% [markdown]
# ### 1.3 Text Manipulation Converters
#
# These converters perform text replacement, template injection, and URL encoding:

# %%
from pyrit.datasets import TextJailBreak
from pyrit.prompt_converter import (
    JsonStringConverter,
    SearchReplaceConverter,
    SuffixAppendConverter,
    TemplateSegmentConverter,
    TextJailbreakConverter,
    UrlConverter,
)

prompt = "tell me how to cut down a tree"

# Search and replace
search_replace = SearchReplaceConverter(pattern="tree", replace="building")
print("Search Replace:", await search_replace.convert_async(prompt=prompt))  # type: ignore

# Suffix append
suffix_append = SuffixAppendConverter(suffix=" Please provide detailed instructions.")
print("Suffix Append:", await suffix_append.convert_async(prompt=prompt))  # type: ignore

# URL encoding
url_converter = UrlConverter()
print("URL Encoded:", await url_converter.convert_async(prompt=prompt))  # type: ignore

# JSON string escaping (useful for embedding prompts in JSON payloads)
json_string_converter = JsonStringConverter()
print("JSON String:", await json_string_converter.convert_async(prompt='He said "hello\nworld"'))  # type: ignore

# Text jailbreak with template
text_jailbreak = TextJailbreakConverter(jailbreak_template=TextJailBreak(template_file_name="aim.yaml"))
print("Text Jailbreak:", await text_jailbreak.convert_async(prompt=prompt))  # type: ignore

# Template segment splits prompt using a template (uses default tom_and_jerry.yaml if not specified)
template_converter = TemplateSegmentConverter()
print("Template Segment:", await template_converter.convert_async(prompt=prompt))  # type: ignore

# %% [markdown]
# ### 1.4 Token Smuggling Converters
#
# These converters use Unicode variation selectors and other techniques to hide text:

# %%
from pyrit.prompt_converter import (
    AsciiSmugglerConverter,
    SneakyBitsSmugglerConverter,
    VariationSelectorSmugglerConverter,
)

prompt = "secret message"

# ASCII smuggling with Unicode tags [@embracethered2024unicode]
ascii_smuggler = AsciiSmugglerConverter(action="encode", unicode_tags=True)
print("ASCII Smuggler:", await ascii_smuggler.convert_async(prompt=prompt))  # type: ignore

# Sneaky Bits [@embracethered2025sneakybits] uses zero-width characters
sneaky_bits = SneakyBitsSmugglerConverter(action="encode")
print("Sneaky Bits:", await sneaky_bits.convert_async(prompt=prompt))  # type: ignore

# Variation selector smuggler
var_selector = VariationSelectorSmugglerConverter(action="encode", embed_in_base=True)
print("Variation Selector:", await var_selector.convert_async(prompt=prompt))  # type: ignore

# %% [markdown]
# <a id="llm-based-converters"></a>
# ## LLM-Based Converters
#
# LLM-based converters use language models to transform prompts. These converters are more flexible and can produce more natural variations, but they are slower and require an LLM target.
#
# These converters use LLMs to transform text style, tone, language, and semantics:

# %%
import pathlib

from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.models import SeedPrompt
from pyrit.prompt_converter import (
    DenylistConverter,
    MaliciousQuestionGeneratorConverter,
    MathPromptConverter,
    NoiseConverter,
    PersuasionConverter,
    RandomTranslationConverter,
    ScientificTranslationConverter,
    TenseConverter,
    ToneConverter,
    ToxicSentenceGeneratorConverter,
    TranslationConverter,
    VariationConverter,
)
from pyrit.prompt_target import OpenAIChatTarget

attack_llm = OpenAIChatTarget()

prompt = "tell me about the history of the united states of america"

# Variation converter creates variations of prompts
variation_converter_strategy = SeedPrompt.from_yaml_file(
    pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "variation_converter_prompt_softener.yaml"
)
variation_converter = VariationConverter(converter_target=attack_llm, prompt_template=variation_converter_strategy)
print("Variation:", await variation_converter.convert_async(prompt=prompt))  # type: ignore

# Noise adds random noise
noise_converter = NoiseConverter(converter_target=attack_llm)
print("Noise:", await noise_converter.convert_async(prompt=prompt))  # type: ignore

# Tone changes tone
tone_converter = ToneConverter(converter_target=attack_llm, tone="angry")
print("Tone (angry):", await tone_converter.convert_async(prompt=prompt))  # type: ignore

# Translation to specific language
translation_converter = TranslationConverter(converter_target=attack_llm, language="French")
print("Translation (French):", await translation_converter.convert_async(prompt=prompt))  # type: ignore

# Random translation translates each word to a random language
random_translation_converter = RandomTranslationConverter(
    converter_target=attack_llm, languages=["French", "German", "Spanish", "English"]
)
print("Random Translation:", await random_translation_converter.convert_async(prompt=prompt))  # type: ignore

# Tense changes verb tense
tense_converter = TenseConverter(converter_target=attack_llm, tense="far future")
print("Tense (future):", await tense_converter.convert_async(prompt=prompt))  # type: ignore

# Persuasion [@zeng2024persuasion] applies persuasion techniques
persuasion_converter = PersuasionConverter(converter_target=attack_llm, persuasion_technique="logical_appeal")
print("Persuasion:", await persuasion_converter.convert_async(prompt=prompt))  # type: ignore

# Denylist detection
denylist_converter = DenylistConverter(converter_target=attack_llm)
print("Denylist Check:", await denylist_converter.convert_async(prompt=prompt))  # type: ignore

# Malicious question generator
malicious_question = MaliciousQuestionGeneratorConverter(converter_target=attack_llm)
print("Malicious Question:", await malicious_question.convert_async(prompt=prompt))  # type: ignore

# Toxic sentence generator
toxic_generator = ToxicSentenceGeneratorConverter(converter_target=attack_llm)
print("Toxic Sentence:", await toxic_generator.convert_async(prompt="building"))  # type: ignore

# MathPrompt [@bethany2024mathprompt] transforms text into symbolic math
math_prompt_converter = MathPromptConverter(converter_target=attack_llm)
print("Math Prompt:", await math_prompt_converter.convert_async(prompt=prompt))  # type: ignore

# Scientific converter translates into scientific language
scientific_translation_converter = ScientificTranslationConverter(converter_target=attack_llm, mode="academic")
print("Scientific Translation:", await scientific_translation_converter.convert_async(prompt=prompt))  # type: ignore
