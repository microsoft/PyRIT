# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.17.3
# ---
# %% [markdown]
# # Selectively Converting
#
# Use selective conversion to change part of a prompt while keeping the surrounding instructions unchanged.
# This guide runs locally: it uses Base64, ROT13, and a `TextTarget`, not an LLM or API credentials.
#
# | Goal | Use |
# |---|---|
# | Select text you already know | Put `⟪` and `⟫` around each region. |
# | Apply several stages to the same regions | Use `keep_tokens=True` in direct calls, or a preserving selective wrapper in a pipeline. |
# | Consume one selection layer per stage | Nest marker pairs in the input. |
# | Select words or a character range programmatically | Give `SelectiveTextConverter` a selection strategy. |
#
# On an ordinary converter, `convert_async` changes the whole value, including marker characters.
# Use `convert_tokens_async` to select marked regions.
# `SelectiveTextConverter.convert_async` instead applies its configured selection strategy.
# Attack converter pipelines call `convert_tokens_async` automatically.

# %%
from io import StringIO

from pyrit.converter import (
    Base64Converter,
    IndexSelectionStrategy,
    ROT13Converter,
    SelectiveTextConverter,
    TokenSelectionStrategy,
    WordPositionSelectionStrategy,
    WordRegexSelectionStrategy,
)
from pyrit.executor.attack import AttackConverterConfig, PromptSendingAttack
from pyrit.prompt_normalizer import ConverterConfiguration, PromptNormalizer
from pyrit.prompt_target import TextTarget
from pyrit.setup import IN_MEMORY, initialize_pyrit_async

# %% [markdown]
# ## 1. Convert Only Marked Text
#
# Mark each region separately. The converter removes the selected marker pairs and leaves all other text unchanged.
# Multiple regions, line breaks, and empty regions are supported. Every opening marker must have a closing marker.

# %%
result = await Base64Converter().convert_tokens_async(  # type: ignore
    prompt="Decode: ⟪hello⟫ and ⟪world⟫"
)
print(result.output_text)
assert result.output_text == "Decode: aGVsbG8= and d29ybGQ="

# %% [markdown]
# **Important:** once all markers are gone, the next converter converts the whole value.
# A single pair does not keep a region selected for an entire chain.

# %%
whole_result = await ROT13Converter().convert_tokens_async(prompt=result.output_text)  # type: ignore
print(whole_result.output_text)
assert whole_result.output_text.startswith("Qrpbqr:")

# %% [markdown]
# ## 2. Keep the Same Regions Selected Across Stages
#
# For direct calls, set `keep_tokens=True` on `convert_tokens_async`.
# The shared parser retains each selected pair without changing the surrounding text.
# With no markers, it wraps the whole text result; non-text outputs are not wrapped.
# The default is `False`, so the final call below consumes the pairs.

# %%
kept = await Base64Converter().convert_tokens_async(  # type: ignore
    prompt="Decode: ⟪hello⟫ and ⟪world⟫", keep_tokens=True
)
print(kept.output_text)
assert kept.output_text == "Decode: ⟪aGVsbG8=⟫ and ⟪d29ybGQ=⟫"

completed = await ROT13Converter().convert_tokens_async(prompt=kept.output_text)  # type: ignore
print(completed.output_text)
assert completed.output_text == "Decode: nTIfoT8= and q29loTD="

# %% [markdown]
# For attack and API pipelines, configure a selective wrapper:
# `preserve_tokens=True` keeps a marker pair around each converted region at its original position.
# A later `TokenSelectionStrategy` finds those regions without selecting the surrounding instructions.
# Use `preserve_tokens=False` on the final stage to consume the pairs.
# This setting uses the same shared retention logic as `keep_tokens`.
#
# This example selects the second half by word position, then applies three transformations.
# Each token-based stage converts the regions produced by the first stage.

# %%
select_words = SelectiveTextConverter(
    sub_converter=Base64Converter(),
    selection_strategy=WordPositionSelectionStrategy(start_proportion=0.5, end_proportion=1.0),
    preserve_tokens=True,
)
rotate_selected = SelectiveTextConverter(
    sub_converter=ROT13Converter(),
    selection_strategy=TokenSelectionStrategy(),
    preserve_tokens=True,
)
encode_selected = SelectiveTextConverter(
    sub_converter=Base64Converter(),
    selection_strategy=TokenSelectionStrategy(),
    preserve_tokens=False,
)
selective_chain = [select_words, rotate_selected, encode_selected]
expected_stages = [
    "tell me how ⟪dG8=⟫ ⟪ZG8=⟫ ⟪aXQ=⟫",
    "tell me how ⟪qT8=⟫ ⟪MT8=⟫ ⟪nKD=⟫",
    "tell me how cVQ4PQ== TVQ4PQ== bktEPQ==",
]
value = "tell me how to do it"
for converter, expected in zip(selective_chain, expected_stages, strict=True):
    value = (await converter.convert_async(prompt=value)).output_text  # type: ignore
    print(value)
    assert value == expected

# %% [markdown]
# Without markers, a token-selection wrapper converts the whole prompt.
# If `preserve_tokens=True`, it wraps that whole result. It does not guess which words you intended to select.

# %%
unmarked = await rotate_selected.convert_async(prompt="hello")  # type: ignore
print(unmarked.output_text)
assert unmarked.output_text == "⟪uryyb⟫"

# %% [markdown]
# ## 3. Use Nested Markers for a Fixed Number of Stages
#
# Every stage processes all innermost regions in parallel. A parent region waits for a later stage.
# An ordinary converter consumes one pair from each selected region; a preserving wrapper consumes none.
# Explicit nesting in the input is never collapsed.
#
# Two pairs per region keep `Decode:` unchanged through Base64 and ROT13:

# %%
value = "Decode: ⟪⟪hello⟫⟫ and ⟪⟪world⟫⟫"
expected_stages = [
    "Decode: ⟪aGVsbG8=⟫ and ⟪d29ybGQ=⟫",
    "Decode: nTIfoT8= and q29loTD=",
]
for converter, expected in zip([Base64Converter(), ROT13Converter()], expected_stages, strict=True):
    value = (await converter.convert_tokens_async(prompt=value)).output_text  # type: ignore
    print(value)
    assert value == expected

# %% [markdown]
# For **Translate to French -> Base64 -> ROT13**, use three pairs per region:
#
# ```text
# Decode this recursively: ⟪⟪⟪Hello⟫⟫⟫ and ⟪⟪⟪Goodbye⟫⟫⟫
# ```
#
# If translation returns `Bonjour` and `Au revoir`, the stages produce:
#
# | Stage | First region | Second region |
# |---|---|---|
# | Translate to French | `⟪⟪Bonjour⟫⟫` | `⟪⟪Au revoir⟫⟫` |
# | Base64 | `⟪Qm9uam91cg==⟫` | `⟪QXUgcmV2b2ly⟫` |
# | ROT13 | `Dz9hnz91pt==` | `DKHtpzI2o2yl` |
#
# The prefix and ` and ` stay unchanged. This table does not call a live translation target.
#
# Regions can have different depths. A completed region stays unchanged while other marked regions remain.
# When no markers remain anywhere, later stages convert the whole prompt.
# If the chain stops before all layers are consumed, the remaining markers stay in the result.

# %% [markdown]
# ## 4. Select Words or Characters Programmatically
#
# Use a word strategy for separate words, or a character strategy for one continuous range.
# A selection strategy runs on the value entering that stage, not on the original prompt.
# Translation and other transformations can change word counts or character positions.
#
# | Strategy | Selection |
# |---|---|
# | `WordIndexSelectionStrategy` | Zero-based word indices. |
# | `WordKeywordSelectionStrategy` | Words matching configured keywords. |
# | `WordRegexSelectionStrategy` | Words matching a regular expression. |
# | `WordPositionSelectionStrategy` | A range of proportional word positions. |
# | `WordProportionSelectionStrategy` | A random proportion of words; set a seed for repeatable selection. |
# | `IndexSelectionStrategy` | A continuous character range; the end is exclusive. |
# | `TokenSelectionStrategy` | Innermost regions marked by an earlier stage or by the input. |
#
# Word selection uses a space as its default separator. Set `word_separator` when your input uses another separator.
# Character selection can operate on phrases or multiline text.

# %%
numbers = SelectiveTextConverter(
    sub_converter=Base64Converter(),
    selection_strategy=WordRegexSelectionStrategy(pattern=r"\d+"),
)
print((await numbers.convert_async(prompt="Codes 123 and 456 stay separate")).output_text)  # type: ignore

characters = SelectiveTextConverter(
    sub_converter=ROT13Converter(),
    selection_strategy=IndexSelectionStrategy(start=5, end=10),
)
character_result = await characters.convert_async(prompt="keep hello unchanged")  # type: ignore
print(character_result.output_text)
assert character_result.output_text == "keep uryyb unchanged"

# %% [markdown]
# ## 5. Put the Chain in an Attack
#
# Initialize PyRIT before constructing a target, normalizer, or attack.
# `ConverterConfiguration.from_converters` retains the converter order.
# The attack normalizer applies the same marker rules as the direct examples.
# Request converters change the outbound prompt; response converters, if configured, change the target reply.
#
# `TextTarget` writes the converted request locally and returns no reply.
# Replace it with your configured model target when you want to send the request to an LLM.

# %%
await initialize_pyrit_async(memory_db_type=IN_MEMORY)  # type: ignore
stream = StringIO()
target = TextTarget(text_stream=stream)
attack = PromptSendingAttack(
    objective_target=target,
    attack_converter_config=AttackConverterConfig(
        request_converters=ConverterConfiguration.from_converters(converters=selective_chain)
    ),
)
await attack.execute_async(objective="tell me how to do it")  # type: ignore
print(stream.getvalue().strip())
assert "tell me how cVQ4PQ== TVQ4PQ== bktEPQ==" in stream.getvalue()

# %% [markdown]
# ## 6. Configure Custom Markers
#
# Unicode markers are defaults, not a requirement. Use a distinct, non-empty opening and closing string.
# Identical opening and closing strings form alternating flat pairs and cannot express nesting.
# Longer markers reduce accidental matches but do not eliminate them.
#
# Use the same marker pair on a token-selection wrapper and its attack's `PromptNormalizer`.
# An explicit `convert_tokens_async` call uses its `start_token` and `end_token` arguments for retained pairs.
# On a selective wrapper, either `keep_tokens=True` or configured `preserve_tokens=True` retains the pair.
# An empty normalizer marker is rejected at construction, before any pipeline runs.
# The GUI selection button still inserts the default Unicode pair.

# %%
start_token, end_token = "<|pyrit_start_8f3a|>", "<|pyrit_end_8f3a|>"
custom_prompt = f"Decode: {start_token}{start_token}hello{end_token}{end_token}"
custom_result = await Base64Converter().convert_tokens_async(  # type: ignore
    prompt=custom_prompt, start_token=start_token, end_token=end_token
)
print(custom_result.output_text)
assert custom_result.output_text == f"Decode: {start_token}aGVsbG8={end_token}"

stream = StringIO()
custom_attack = PromptSendingAttack(
    objective_target=TextTarget(text_stream=stream),
    prompt_normalizer=PromptNormalizer(start_token=start_token, end_token=end_token),
    attack_converter_config=AttackConverterConfig(
        request_converters=ConverterConfiguration.from_converters(converters=[Base64Converter(), ROT13Converter()])
    ),
)
await custom_attack.execute_async(objective=custom_prompt)  # type: ignore
print(stream.getvalue().strip())
assert "Decode: nTIfoT8=" in stream.getvalue()

# %% [markdown]
# API clients can set `start_token` and `end_token` on `ConverterPreviewRequest` and `AddMessageRequest`,
# including queued sends. The same settings apply to request and response converter chains.
# A preconverted message piece is sent as supplied; the backend does not run request converters on it again.
#
# Choose markers that are unlikely to appear in prompts or replies.
# If response converters are configured, an unmatched marker in a reply raises before that reply is stored.
# Text such as `echo x >> log` or a Python `>>>` prompt is literal when using the longer markers above.

# %% [markdown]
# ## 7. Compose Wrappers Without Adding Selection Layers
#
# Wrapping a token-selection wrapper in another token-selection wrapper with the same markers
# does not create a new selection. If either wrapper preserves tokens, they retain one pair for that selection.
# Explicit input layers remain intact. Different marker pairs and programmatic selection strategies
# are separate selections; their boundaries are not collapsed.
#
# Converter-generated marker text is not evidence that a wrapper already preserved its boundaries.
# The wrapper keeps its own pair, and generated markers are not processed again during the same call.

# %%
inner = SelectiveTextConverter(
    sub_converter=ROT13Converter(),
    selection_strategy=TokenSelectionStrategy(),
    preserve_tokens=True,
)
outer = SelectiveTextConverter(
    sub_converter=inner,
    selection_strategy=TokenSelectionStrategy(),
    preserve_tokens=True,
)
for prompt, expected in [
    ("prefix ⟪word⟫ suffix", "prefix ⟪jbeq⟫ suffix"),
    ("prefix ⟪⟪word⟫⟫ suffix", "prefix ⟪⟪jbeq⟫⟫ suffix"),
]:
    result = await outer.convert_async(prompt=prompt)  # type: ignore
    print(result.output_text)
    assert result.output_text == expected

# %% [markdown]
# ## Limits and Custom Converters
#
# Selective conversion requires text input and output. Use ordinary converters for whole-value media conversion.
# Malformed markers raise before any selected region is converted.
# For the same seed and execution context, native token-selection calls produce the same result
# through direct calls, attack pipelines, and API previews.
#
# A custom converter can override `convert_tokens_async` to own whole-prompt behavior.
# A token-selection wrapper with `preserve_tokens=False` and the default `keep_tokens=False`
# delegates to that override without new arguments.
# The custom override, not the wrapper, then determines which text changes.
# Requesting `preserve_tokens=True` or `keep_tokens=True` around such an override is rejected before it runs:
# the wrapper cannot recover
# selected-region boundaries from an arbitrary returned string.
# For per-region preservation, implement the transformation in `convert_async` and use the shared token parser.
# No signature probing, whole-prompt preservation fallback, or additional pipeline framework is needed.

# %% [markdown]
# ## Contributors
#
# [**Utkarsh Bahuguna (u7k4rs6)**](https://github.com/u7k4rs6) contributed the original
# per-region marker-preservation fix and composition regression cases in
# [#2893](https://github.com/microsoft/PyRIT/pull/2893).
